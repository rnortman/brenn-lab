//! The client against the stand-in server, in one process: credits, the
//! delay's formula and countdown, the bounds on a write and a drain, the
//! server's news about the stream, the refusals, and the line protocol.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use pulse_stand_in::{Config, Instruction, SinkSpec, StandIn};
use serde_json::{Value, json};

use super::{Answer, COOKIE_BYTES, Client, Refusal, cookie, deadline, serve};
use crate::conn::{Conn, Fail};

const SINK: &str = "stand-in.analog";
const OTHER: &str = "stand-in.other";
const RATE: u32 = 48_000;
/// Two channels of 16 bits.
const FRAME: usize = 4;
/// 10 ms at 48 kHz.
const PERIOD: u64 = 480;
/// 100 ms at 48 kHz.
const BUFFER: u64 = 4_800;

struct Bench {
    stand_in: StandIn,
    client: Client,
    dir: PathBuf,
}

impl Drop for Bench {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

impl Bench {
    fn instruct(&self, instruction: Value) -> Value {
        let instruction: Instruction = serde_json::from_value(instruction).expect("an instruction");
        self.stand_in.instruct(instruction).expect("carried out")
    }

    fn frames_kept(&self) -> Vec<u8> {
        std::fs::read(self.dir.join("frames")).expect("the frames file")
    }

    fn open(&mut self) -> Value {
        ok(self.client.open(SINK, RATE, 2, "s16_le", PERIOD, BUFFER))
    }
}

/// A stand-in with two stereo sinks, the first with `latency_usec` of
/// latency, and a client connected to it.
fn bench(speed: f64, latency_usec: u64) -> Bench {
    static NEXT: AtomicU32 = AtomicU32::new(0);
    // A Unix socket's path is at most 108 bytes; the test's own directory is
    // longer, so the stand-in's is under /tmp.
    let dir = PathBuf::from(format!(
        "/tmp/pulse-client-test-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&dir).expect("a directory under /tmp");
    let sinks: Vec<SinkSpec> = serde_json::from_value(json!([
        {"name": SINK, "description": "Analog", "rate_hz": RATE, "channels": 2,
         "format": "s32le", "latency_usec": latency_usec},
        {"name": OTHER, "rate_hz": 44_100, "channels": 1, "format": "float32le",
         "base_volume": 30_000, "hardware_volume": true},
    ]))
    .expect("sinks");
    let socket = dir.join("native");
    let stand_in = StandIn::start(Config {
        socket: socket.clone(),
        sinks,
        speed,
        frames: dir.join("frames"),
    })
    .expect("the stand-in starts");
    let conn =
        Conn::connect(&socket, vec![0; COOKIE_BYTES], deadline()).expect("the client connects");
    Bench {
        stand_in,
        client: Client { conn },
        dir,
    }
}

fn ok(answer: Answer) -> Value {
    match answer {
        Ok(value) => value,
        Err(Refusal(error, detail)) => panic!("refused: {error}: {detail}"),
    }
}

fn refused(answer: Answer) -> (&'static str, String) {
    match answer {
        Ok(value) => panic!("answered {value}, not refused"),
        Err(Refusal(error, detail)) => (error, detail),
    }
}

/// `frames` frames of a pattern no two frames of which are alike.
fn source(frames: usize) -> Vec<u8> {
    (0..frames)
        .flat_map(|i| u32::try_from(i).expect("few frames").to_le_bytes())
        .collect()
}

#[test]
fn with_no_cookie_file_a_zeroed_cookie_is_sent_and_an_empty_one_is_refused() {
    let b = bench(1.0, 0);
    let socket = b.dir.join("native");
    assert!(Conn::connect(&socket, Vec::new(), deadline()).is_err());
    let zeroed = vec![0; COOKIE_BYTES];
    assert_eq!(cookie(None), Ok(zeroed.clone()));
    assert_eq!(
        cookie(Some(b.dir.clone().into_os_string())),
        Ok(zeroed.clone())
    );
    assert!(Conn::connect(&socket, zeroed, deadline()).is_ok());
}

#[test]
fn a_server_that_answers_nothing_fails_the_handshake_by_its_one_deadline() {
    let b = bench(1.0, 0);
    let socket = b.dir.join("silent");
    let _listener = std::os::unix::net::UnixListener::bind(&socket).expect("a silent socket");
    let started = Instant::now();
    let by = started + Duration::from_millis(300);
    match Conn::connect(&socket, vec![0; COOKIE_BYTES], by) {
        Err(Fail::Timeout) => {}
        Err(fail) => panic!("failed otherwise: {fail}"),
        Ok(_) => panic!("connected to a server that answers nothing"),
    }
    let took = started.elapsed();
    assert!(
        took >= Duration::from_millis(300) && took < Duration::from_secs(2),
        "{took:?}"
    );
}

#[test]
fn sinks_answer_as_the_server_states_them() {
    let mut b = bench(1.0, 10_000);
    let sinks = ok(b.client.sinks());
    let sinks = sinks["sinks"].as_array().expect("a list");
    assert_eq!(sinks.len(), 2);
    assert_eq!(sinks[0]["name"], SINK);
    assert_eq!(sinks[0]["description"], "Analog");
    assert_eq!(sinks[0]["format"], "s32le");
    assert_eq!(sinks[0]["positions"], json!(["front-left", "front-right"]));
    assert_eq!(sinks[0]["volumes"], json!([65_536, 65_536]));
    assert_eq!(sinks[0]["decibel_volume"], true);
    assert_eq!(sinks[0]["flat_volume"], false);
    assert_eq!(sinks[1]["description"], Value::Null);
    assert_eq!(sinks[1]["format"], "float32le");
    assert_eq!(sinks[1]["positions"], json!(["mono"]));
    assert_eq!(sinks[1]["base_volume"], 30_000);
    assert_eq!(sinks[1]["hardware_volume"], true);
    assert_eq!(ok(b.client.sink(OTHER))["sink"], sinks[1]);
}

#[test]
fn a_sink_set_reads_back() {
    let mut b = bench(1.0, 0);
    ok(b.client
        .set_sink(SINK, Some(vec![30_000, 40_000]), Some(true)));
    let sink = ok(b.client.sink(SINK));
    assert_eq!(sink["sink"]["volumes"], json!([30_000, 40_000]));
    assert_eq!(sink["sink"]["muted"], true);
    // A volume per sink channel or the server refuses.
    let (error, _) = refused(b.client.set_sink(SINK, Some(vec![30_000]), None));
    assert_eq!(error, "server");
}

#[test]
fn an_absent_sink_is_no_sink() {
    let mut b = bench(1.0, 0);
    assert_eq!(refused(b.client.sink("absent")).0, "no_sink");
    assert_eq!(
        refused(b.client.set_sink("absent", None, Some(false))).0,
        "no_sink"
    );
    let open = b.client.open("absent", RATE, 2, "s16_le", PERIOD, BUFFER);
    assert_eq!(refused(open).0, "no_sink");
}

#[test]
fn the_stream_is_made_corked_at_0_db_unmuted_on_the_named_sink() {
    let mut b = bench(1.0, 0);
    let granted = b.open();
    assert_eq!(granted["sink"], SINK);
    assert_eq!(granted["sample_format"], "s16_le");
    assert_eq!(granted["buffer_frames"], BUFFER);
    assert_eq!(granted["period_frames"], PERIOD);
    let stream = ok(b.client.stream_state());
    assert_eq!(stream["sink"], SINK);
    assert_eq!(stream["volumes"], json!([65_536, 65_536]));
    assert_eq!(stream["muted"], false);
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"][0]["corked"], true);
    assert_eq!(state["streams_made"], 1);
}

#[test]
fn a_server_that_ignores_the_volume_asked_for_is_seen() {
    let mut b = bench(1.0, 0);
    b.instruct(json!({"op": "ignore_initial_volume", "volume": 32_768}));
    b.open();
    let stream = ok(b.client.stream_state());
    assert_eq!(stream["volumes"], json!([32_768, 32_768]));
}

#[test]
fn credit_bounds_a_write_and_every_frame_reaches_the_server() {
    let mut b = bench(4.0, 0);
    b.open();
    let data = source(3 * BUFFER as usize);
    // Corked, the server has asked for one buffer and no more.
    let first = ok(b.client.write(&data));
    assert_eq!(first["frames"], BUFFER);
    let mut sent = BUFFER as usize * FRAME;
    while sent < data.len() {
        let taken = ok(b.client.write(&data[sent..]))["frames"]
            .as_u64()
            .expect("frames");
        assert!(taken > 0);
        sent += usize::try_from(taken).expect("frames") * FRAME;
    }
    ok(b.client.drain());
    assert_eq!(b.frames_kept(), data);
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"][0]["played_frames"], 3 * BUFFER);
    ok(b.client.close());
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"], json!([]));
}

#[test]
fn a_second_stream_starts_with_none_of_the_first_ones_state() {
    let mut b = bench(1.0, 0);
    b.instruct(json!({"op": "stall", "stalled": true}));
    b.open();
    ok(b.client.write(&source(PERIOD as usize)));
    b.instruct(json!({"op": "underflow"}));
    ok(b.client.close());
    b.open();
    // Corked again, with the second stream's own credit of one buffer and
    // no underflow of the first's.
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"][0]["corked"], true);
    let first = ok(b.client.write(&source(2 * BUFFER as usize)));
    assert_eq!(first["frames"], BUFFER);
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"][0]["corked"], false);
    assert_eq!(state["streams_made"], 2);
}

#[test]
fn delay_is_the_buffer_plus_the_sink_latency() {
    // 10 ms of sink latency is 480 frames.
    let mut b = bench(1.0, 10_000);
    b.open();
    b.instruct(json!({"op": "stall", "stalled": true}));
    ok(b.client.write(&source(1_000)));
    assert_eq!(ok(b.client.delay())["frames"], 1_000 + 480);
    // A dropped buffer leaves the sink's latency alone.
    ok(b.client.drop_buffer());
    assert_eq!(ok(b.client.delay())["frames"], 480);
}

#[test]
fn delay_counts_the_sink_latency_down_once_the_buffer_is_empty() {
    // 200 ms of sink latency is 9600 frames.
    let mut b = bench(1.0, 200_000);
    b.open();
    ok(b.client.write(&source(480)));
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let state = b.instruct(json!({"op": "state"}));
        if state["streams"][0]["played_frames"] == 480 {
            break;
        }
        assert!(Instant::now() < deadline, "the stand-in played nothing");
        thread::sleep(Duration::from_millis(5));
    }
    // The first reading that sees the buffer empty is the sink's latency.
    assert_eq!(ok(b.client.delay())["frames"], 9_600);
    thread::sleep(Duration::from_millis(100));
    let later = ok(b.client.delay())["frames"].as_u64().expect("frames");
    assert!(later < 9_600 && later > 0, "{later}");
    thread::sleep(Duration::from_millis(150));
    assert_eq!(ok(b.client.delay())["frames"], 0);
}

#[test]
fn a_write_no_credit_comes_for_is_refused_after_the_buffer_and_grace() {
    let mut b = bench(1.0, 0);
    b.open();
    b.instruct(json!({"op": "stall", "stalled": true}));
    ok(b.client.write(&source(BUFFER as usize)));
    let started = Instant::now();
    let (error, detail) = refused(b.client.write(&source(PERIOD as usize)));
    let waited = started.elapsed();
    assert_eq!(error, "stream");
    assert!(detail.contains("asked for no frames"), "{detail}");
    assert!(waited >= Duration::from_millis(2_100), "{waited:?}");
    assert!(waited < Duration::from_secs(4), "{waited:?}");
}

#[test]
fn a_drain_that_never_plays_out_is_refused() {
    let mut b = bench(1.0, 0);
    b.open();
    b.instruct(json!({"op": "stall", "stalled": true}));
    ok(b.client.write(&source(PERIOD as usize)));
    let started = Instant::now();
    let (error, _) = refused(b.client.drain());
    assert_eq!(error, "stream");
    assert!(started.elapsed() >= Duration::from_millis(2_100));
}

#[test]
fn an_underflow_is_answered_once() {
    let mut b = bench(1.0, 0);
    b.open();
    ok(b.client.write(&source(PERIOD as usize)));
    b.instruct(json!({"op": "underflow"}));
    assert_eq!(
        refused(b.client.write(&source(PERIOD as usize))).0,
        "underrun"
    );
    ok(b.client.write(&source(PERIOD as usize)));
}

#[test]
fn a_killed_stream_fails_its_next_write() {
    let mut b = bench(1.0, 0);
    b.open();
    ok(b.client.write(&source(PERIOD as usize)));
    b.instruct(json!({"op": "kill_stream"}));
    let (error, detail) = refused(b.client.write(&source(PERIOD as usize)));
    assert_eq!(error, "stream");
    assert!(detail.contains("killed"), "{detail}");
}

#[test]
fn a_moved_stream_fails_its_next_write_and_read() {
    let mut b = bench(1.0, 0);
    b.open();
    ok(b.client.write(&source(PERIOD as usize)));
    b.instruct(json!({"op": "move_stream", "sink": OTHER}));
    let (error, detail) = refused(b.client.write(&source(PERIOD as usize)));
    assert_eq!(error, "stream");
    assert!(detail.contains(OTHER), "{detail}");
    assert_eq!(refused(b.client.stream_state()).0, "stream");
}

#[test]
fn a_suspension_breaks_only_a_stream_that_plays() {
    let mut b = bench(1.0, 0);
    b.open();
    b.instruct(json!({"op": "suspend_stream"}));
    ok(b.client.stream_state());
    ok(b.client.write(&source(PERIOD as usize)));
    b.instruct(json!({"op": "suspend_stream"}));
    let (error, detail) = refused(b.client.write(&source(PERIOD as usize)));
    assert_eq!(error, "stream");
    assert!(detail.contains("suspended"), "{detail}");
}

#[test]
fn a_removed_sink_kills_the_stream_and_is_no_sink() {
    let mut b = bench(1.0, 0);
    b.open();
    ok(b.client.write(&source(PERIOD as usize)));
    b.instruct(json!({"op": "remove_sink", "sink": SINK}));
    assert_eq!(
        refused(b.client.write(&source(PERIOD as usize))).0,
        "stream"
    );
    assert_eq!(refused(b.client.sink(SINK)).0, "no_sink");
}

#[test]
fn a_tail_shorter_than_one_request_plays_out_and_the_delay_reaches_0() {
    let mut b = bench(1.0, 0);
    b.open();
    ok(b.client.write(&source(PERIOD as usize / 2)));
    let deadline = Instant::now() + Duration::from_secs(1);
    while ok(b.client.delay())["frames"] != 0 {
        assert!(Instant::now() < deadline, "the tail was held unplayed");
        thread::sleep(Duration::from_millis(5));
    }
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"][0]["played_frames"], PERIOD / 2);
}

#[test]
fn the_first_write_sends_its_frames_before_it_uncorks_the_stream() {
    let mut b = bench(1.0, 0);
    b.open();
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"][0]["taken_frames_at_uncork"], Value::Null);
    ok(b.client.write(&source(PERIOD as usize)));
    ok(b.client.write(&source(PERIOD as usize)));
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"][0]["corked"], false);
    assert_eq!(state["streams"][0]["taken_frames_at_uncork"], PERIOD);
}

#[test]
fn a_stream_never_written_to_has_no_delay_and_drains_at_once() {
    let mut b = bench(1.0, 10_000);
    b.open();
    assert_eq!(ok(b.client.delay())["frames"], 0);
    let started = Instant::now();
    ok(b.client.drain());
    assert!(started.elapsed() < Duration::from_secs(1));
    ok(b.client.close());
}

#[test]
fn delay_is_unknown_when_the_server_refuses_the_latency_query() {
    let mut b = bench(1.0, 0);
    b.open();
    b.instruct(json!({"op": "stall", "stalled": true}));
    ok(b.client.write(&source(PERIOD as usize)));
    // The server answers a query on a stream it killed with an error.
    b.instruct(json!({"op": "kill_stream"}));
    assert_eq!(ok(b.client.delay())["frames"], Value::Null);
}

#[test]
fn an_open_the_protocol_cannot_carry_is_params() {
    let mut b = bench(1.0, 0);
    for (channels, format) in [(2, "f64_le"), (0, "s16_le"), (33, "s16_le")] {
        let answer = b.client.open(SINK, RATE, channels, format, PERIOD, BUFFER);
        assert_eq!(refused(answer).0, "params", "{channels} {format}");
    }
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams_made"], 0);
}

#[test]
fn a_spec_other_than_the_one_asked_for_is_params_and_leaves_no_stream() {
    let mut b = bench(1.0, 0);
    b.instruct(json!({"op": "grant_rate", "rate_hz": 44_100}));
    let (error, detail) = refused(b.client.open(SINK, RATE, 2, "s16_le", PERIOD, BUFFER));
    assert_eq!(error, "params");
    assert!(detail.contains("44100"), "{detail}");
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"], json!([]));
    assert_eq!(state["streams_made"], 1);
}

#[test]
fn a_request_out_of_turn_is_refused() {
    let mut b = bench(1.0, 0);
    let frame = source(1);
    let no_stream = [
        b.client.write(&frame),
        b.client.delay(),
        b.client.drain(),
        b.client.stream_state(),
        b.client.drop_buffer(),
        b.client.close(),
    ];
    for answer in no_stream {
        assert_eq!(
            refused(answer),
            ("request", "no stream is open".to_string())
        );
    }
    b.open();
    assert_eq!(
        refused(b.client.open(SINK, RATE, 2, "s16_le", PERIOD, BUFFER)).0,
        "request"
    );
    // A write is whole frames, at least one.
    assert_eq!(refused(b.client.write(&[])).0, "request");
    assert_eq!(refused(b.client.write(&frame[..3])).0, "request");
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"][0]["taken_frames"], 0);
    assert_eq!(state["streams"][0]["corked"], true);
}

#[test]
fn the_line_protocol_answers_each_request_and_closes_the_stream_at_the_end() {
    let mut b = bench(1.0, 0);
    b.instruct(json!({"op": "stall", "stalled": true}));
    let data = source(PERIOD as usize);
    let mut input = Vec::new();
    let open = json!({"op": "open", "sink": SINK, "rate_hz": RATE, "channels": 2,
                      "sample_format": "s16_le", "period_frames": PERIOD,
                      "buffer_frames": BUFFER});
    input.extend(format!("{open}\n").bytes());
    input.extend(format!("{{\"op\": \"write\", \"bytes\": {}}}\n", data.len()).bytes());
    input.extend(&data);
    input.extend(b"{\"op\": \"delay\"}\nnot a request\n{\"op\": \"stream\"}\n");
    let mut out = Vec::new();
    serve(&mut b.client, &mut input.as_slice(), &mut out).expect("served");
    let answers: Vec<Value> = String::from_utf8(out)
        .expect("text")
        .lines()
        .map(|line| serde_json::from_str(line).expect("one JSON answer a line"))
        .collect();
    assert_eq!(answers.len(), 5, "{answers:?}");
    assert_eq!(answers[0]["ok"], true);
    assert_eq!(answers[0]["buffer_frames"], BUFFER);
    assert_eq!(answers[1], json!({"ok": true, "frames": PERIOD}));
    assert_eq!(answers[2], json!({"ok": true, "frames": PERIOD}));
    assert_eq!(answers[3]["ok"], false);
    assert_eq!(answers[3]["error"], "request");
    assert_eq!(answers[4]["ok"], true);
    assert_eq!(answers[4]["sink"], SINK);
    assert_eq!(b.frames_kept(), data);
    let state = b.instruct(json!({"op": "state"}));
    assert_eq!(state["streams"], json!([]));
}
