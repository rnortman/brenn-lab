//! pulse-client: plays to one sink of a sound server that speaks the
//! PulseAudio native protocol, for the playback role, which runs it as a
//! child.
//!
//! `pulse-client --socket <path>` connects, authenticates with the cookie at
//! `$HOME/.config/pulse/cookie` if there is one and a zeroed cookie if not,
//! and answers its first line:
//! the client library and the server, or `no_server` before it exits. A
//! cookie file that exists but cannot be read ends it before it connects,
//! with no line on stdout and the path on stderr. It then
//! reads one JSON request a line on stdin and answers each with one JSON line
//! on stdout; a `write` request is followed on stdin by its raw bytes. It
//! serves one stream at a time and exits when stdin closes, deleting the
//! stream if one is open.
//!
//! Every answer carries `ok`. A refusal is `{"ok": false, "error", "detail"}`,
//! the error one of `no_sink`, `params`, `stream`, `underrun`, `server` and
//! `request`.

mod conn;
#[cfg(test)]
mod stand_in_test;
mod wire;

use std::ffi::{CString, OsString};
use std::io::{self, BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::{Duration, Instant, SystemTime};

use pulseaudio::protocol::stream::{BufferAttr, StreamFlags};
use pulseaudio::protocol::{
    ChannelVolume, Command, CorkStreamParams, CreatePlaybackStreamReply, GetSinkInfo,
    LatencyParams, PlaybackLatency, PlaybackStreamParams, Prop, Props, PulseError, SampleSpec,
    ServerInfo, SetDeviceMuteParams, SetDeviceVolumeParams, SinkFlags, SinkInfo, SinkInfoList,
    SinkInputInfo, Volume,
};
use serde::Deserialize;
use serde_json::{Value, json};

use conn::{Conn, Fail, REQUEST_TIMEOUT, Stream};

/// The client library, as the record names it. Its version is the pinned
/// crate's, which the build states.
const LIBRARY: &str = "pulseaudio-rs";
const LIBRARY_VERSION: &str = env!("PULSEAUDIO_CRATE_VERSION");

/// The largest raw volume the protocol carries.
const VOLUME_MAX: u32 = u32::MAX / 2;

/// The most bytes one memory block carries.
const BLOCK_BYTES: u64 = 64 * 1024;

/// How long past the buffer's length a write waits for the server to ask
/// for frames, and a drain for the buffer to play out.
const STREAM_GRACE: Duration = Duration::from_secs(2);

/// The deadline of a request that starts now.
fn deadline() -> Instant {
    Instant::now() + REQUEST_TIMEOUT
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Sinks,
    Sink {
        name: String,
    },
    SetSink {
        name: String,
        volumes: Option<Vec<u32>>,
        muted: Option<bool>,
    },
    Open {
        sink: String,
        rate_hz: u32,
        channels: usize,
        sample_format: String,
        period_frames: u64,
        buffer_frames: u64,
    },
    Stream,
    Write {
        bytes: usize,
    },
    Delay,
    Drain,
    Drop,
    Close,
}

/// A refusal: its error word and what happened.
struct Refusal(&'static str, String);

type Answer = Result<Value, Refusal>;

fn refuse(error: &'static str, detail: impl Into<String>) -> Refusal {
    Refusal(error, detail.into())
}

/// A failed request on a sink by name: `no_sink` when the server has none of
/// that name.
fn on_sink(name: &str, fail: Fail) -> Refusal {
    match fail {
        Fail::Server(PulseError::NoEntity) => refuse("no_sink", format!("no sink is named {name}")),
        fail => refuse("server", fail.to_string()),
    }
}

fn server(fail: Fail) -> Refusal {
    refuse("server", fail.to_string())
}

struct Client {
    conn: Conn,
}

fn text(s: &Option<CString>) -> Value {
    match s {
        Some(s) => Value::String(s.to_string_lossy().into_owned()),
        None => Value::Null,
    }
}

fn raw(volumes: &ChannelVolume) -> Vec<u32> {
    volumes.channels().iter().map(Volume::as_u32).collect()
}

fn sink_json(sink: &SinkInfo) -> Value {
    json!({
        "name": sink.name.to_string_lossy(),
        "description": text(&sink.description),
        "rate_hz": sink.sample_spec.sample_rate,
        "channels": sink.sample_spec.channels,
        "format": wire::format_name(sink.sample_spec.format),
        "positions": (&sink.channel_map).into_iter().map(wire::position_name).collect::<Vec<_>>(),
        "volumes": raw(&sink.cvolume),
        "base_volume": sink.base_volume.as_u32(),
        "muted": sink.muted,
        "decibel_volume": sink.flags.contains(SinkFlags::DECIBEL_VOLUME),
        "flat_volume": sink.flags.contains(SinkFlags::FLAT_VOLUME),
        "hardware_volume": sink.flags.contains(SinkFlags::HW_VOLUME_CTRL),
    })
}

fn c_name(name: &str) -> Result<CString, Refusal> {
    CString::new(name).map_err(|_| refuse("request", "a name holds a NUL byte"))
}

impl Client {
    fn stream(&self) -> Result<&Stream, Refusal> {
        self.conn
            .stream
            .as_ref()
            .ok_or_else(|| refuse("request", "no stream is open"))
    }

    fn sinks(&mut self) -> Answer {
        let sinks: SinkInfoList = self
            .conn
            .reply(Command::GetSinkInfoList, deadline())
            .map_err(server)?;
        Ok(json!({"sinks": sinks.iter().map(sink_json).collect::<Vec<_>>()}))
    }

    fn sink(&mut self, name: &str) -> Answer {
        let query = GetSinkInfo {
            index: None,
            name: Some(c_name(name)?),
        };
        let sink: SinkInfo = self
            .conn
            .reply(Command::GetSinkInfo(query), deadline())
            .map_err(|f| on_sink(name, f))?;
        Ok(json!({"sink": sink_json(&sink)}))
    }

    fn set_sink(&mut self, name: &str, volumes: Option<Vec<u32>>, muted: Option<bool>) -> Answer {
        let by = deadline();
        if let Some(volumes) = volumes {
            if volumes.is_empty() || volumes.len() > wire::MAX_CHANNELS {
                return Err(refuse("request", "a volume per channel, 1 to 32"));
            }
            let mut volume = ChannelVolume::empty();
            for v in volumes {
                if v > VOLUME_MAX {
                    return Err(refuse(
                        "request",
                        format!("{v} is above the largest volume"),
                    ));
                }
                volume.push(Volume::from_u32_clamped(v));
            }
            let params = SetDeviceVolumeParams {
                device_index: None,
                device_name: Some(c_name(name)?),
                volume,
            };
            self.conn
                .ack(Command::SetSinkVolume(params), by)
                .map_err(|f| on_sink(name, f))?;
        }
        if let Some(mute) = muted {
            let params = SetDeviceMuteParams {
                device_index: None,
                device_name: Some(c_name(name)?),
                mute,
            };
            self.conn
                .ack(Command::SetSinkMute(params), by)
                .map_err(|f| on_sink(name, f))?;
        }
        Ok(json!({}))
    }

    fn open(
        &mut self,
        sink: &str,
        rate_hz: u32,
        channels: usize,
        sample_format: &str,
        period_frames: u64,
        buffer_frames: u64,
    ) -> Answer {
        if self.conn.stream.is_some() {
            return Err(refuse("request", "a stream is already open"));
        }
        let format = wire::stream_format(sample_format)
            .ok_or_else(|| refuse("params", format!("no format is known for {sample_format}")))?;
        let map = wire::stream_map(channels).ok_or_else(|| {
            refuse(
                "params",
                format!("the protocol carries 1 to 32 channels, not {channels}"),
            )
        })?;
        let count = u8::try_from(channels).map_err(|_| refuse("params", "too many channels"))?;
        let spec = SampleSpec {
            format,
            channels: count,
            sample_rate: rate_hz,
        };
        let frame_bytes = wire::frame_bytes(&spec);
        let bytes = |frames: u64| {
            u32::try_from(frames * frame_bytes)
                .map_err(|_| refuse("params", format!("{frames} frames do not fit the protocol")))
        };
        let mut props = Props::new();
        props.set(Prop::MediaName, c"brenn-lab play");
        let params = PlaybackStreamParams {
            sample_spec: spec,
            channel_map: map,
            sink_index: None,
            sink_name: Some(c_name(sink)?),
            // No pre-buffer: a server with one holds back a buffer's tail
            // shorter than the pre-buffer (PulseAudio) or than one request
            // (PipeWire) until a drain, so the delay would never reach 0.
            // The stream starts when the first write uncorks it after
            // sending its frames, and after an underrun the server plays
            // on, skipping what it missed.
            buffer_attr: BufferAttr {
                max_length: u32::MAX,
                target_length: bytes(buffer_frames)?,
                pre_buffering: 0,
                minimum_request_length: bytes(period_frames)?,
                fragment_size: u32::MAX,
            },
            sync_id: 0,
            cvolume: Some(ChannelVolume::norm(count)),
            props,
            formats: Vec::new(),
            flags: StreamFlags {
                start_corked: true,
                no_move: true,
                start_muted: Some(false),
                ..StreamFlags::default()
            },
        };
        let reply: CreatePlaybackStreamReply = self
            .conn
            .reply(Command::CreatePlaybackStream(params), deadline())
            .map_err(|f| match f {
                Fail::Server(PulseError::NoEntity) => {
                    refuse("no_sink", format!("no sink is named {sink}"))
                }
                Fail::Server(e) => refuse("params", format!("the server refused the stream: {e}")),
                f => server(f),
            })?;
        self.conn.stream = Some(Stream::new(&reply));
        if reply.sample_spec != spec {
            let _ = self.close();
            return Err(refuse(
                "params",
                format!("the server granted {:?}, not {:?}", reply.sample_spec, spec),
            ));
        }
        Ok(json!({
            "sink": text(&reply.sink_name),
            "rate_hz": reply.sample_spec.sample_rate,
            "channels": reply.sample_spec.channels,
            "sample_format": wire::tree_format(reply.sample_spec.format),
            "period_frames": u64::from(reply.buffer_attr.minimum_request_length) / frame_bytes,
            "buffer_frames": u64::from(reply.buffer_attr.target_length) / frame_bytes,
        }))
    }

    fn broken(&self) -> Result<(), Refusal> {
        match &self.stream()?.broken {
            Some(why) => Err(refuse("stream", why.clone())),
            None => Ok(()),
        }
    }

    fn stream_state(&mut self) -> Answer {
        let index = self.stream()?.index;
        self.broken()?;
        let by = deadline();
        let input: SinkInputInfo = self
            .conn
            .reply(Command::GetSinkInputInfo(index), by)
            .map_err(server)?;
        let query = GetSinkInfo {
            index: Some(input.sink_index),
            name: None,
        };
        let sink: SinkInfo = self
            .conn
            .reply(Command::GetSinkInfo(query), by)
            .map_err(server)?;
        Ok(json!({
            "sink": sink.name.to_string_lossy(),
            "volumes": raw(&input.cvolume),
            "muted": input.muted,
        }))
    }

    fn stream_mut(&mut self) -> Result<&mut Stream, Refusal> {
        self.conn
            .stream
            .as_mut()
            .ok_or_else(|| refuse("request", "no stream is open"))
    }

    fn write(&mut self, data: &[u8]) -> Answer {
        let stream = self.stream()?;
        let (channel, frame_bytes) = (stream.channel, stream.frame_bytes());
        let wait = stream.buffer_time() + STREAM_GRACE;
        if data.is_empty() || !(data.len() as u64).is_multiple_of(frame_bytes) {
            return Err(refuse("request", "a write is whole frames, at least one"));
        }
        self.conn.take_pending().map_err(server)?;
        self.broken()?;
        let stream = self.stream_mut()?;
        if stream.underflow {
            stream.underflow = false;
            return Err(refuse("underrun", "the server ran out of frames"));
        }
        let credit_by = Instant::now() + wait;
        while self.stream()?.credit < frame_bytes {
            self.broken()?;
            if !self.conn.wait(credit_by).map_err(server)? {
                return Err(refuse(
                    "stream",
                    format!("the server asked for no frames in {} ms", wait.as_millis()),
                ));
            }
        }
        self.broken()?;
        let credit = self.stream()?.credit;
        let take = (data.len() as u64).min(credit) / frame_bytes * frame_bytes;
        let by = deadline();
        for block in data[..take as usize].chunks(BLOCK_BYTES as usize) {
            self.conn.send(channel, block, by).map_err(server)?;
        }
        let stream = self.stream_mut()?;
        stream.credit -= take;
        // With no pre-buffer the server reads an uncorked stream whether or
        // not it holds frames, and reports no underrun for its start, so the
        // first frames go over while the stream is corked and the uncork
        // follows them.
        if !stream.playing {
            let uncork = CorkStreamParams {
                channel,
                cork: false,
            };
            self.conn
                .ack(Command::CorkPlaybackStream(uncork), by)
                .map_err(server)?;
            self.stream_mut()?.playing = true;
        }
        let stream = self.stream_mut()?;
        stream.empty_since = None;
        Ok(json!({"frames": take / frame_bytes}))
    }

    fn delay(&mut self) -> Answer {
        let stream = self.stream()?;
        let (channel, rate_hz) = (stream.channel, stream.spec.sample_rate);
        let frame_bytes = stream.frame_bytes();
        // A stream never uncorked was handed nothing and plays nothing.
        if !stream.playing {
            return Ok(json!({"frames": 0}));
        }
        if let Some((since, sink_frames)) = stream.empty_since {
            let frames = wire::counted_down(sink_frames, since.elapsed(), rate_hz);
            return Ok(json!({"frames": frames}));
        }
        let query = LatencyParams {
            channel,
            now: SystemTime::now(),
        };
        let latency: PlaybackLatency = match self
            .conn
            .reply(Command::GetPlaybackLatency(query), deadline())
        {
            Ok(latency) => latency,
            Err(Fail::Server(_)) => return Ok(json!({"frames": null})),
            Err(f) => return Err(server(f)),
        };
        let seen = Instant::now();
        let (write_index, read_index) = (latency.write_offset, latency.read_offset);
        let stream = self.stream_mut()?;
        if wire::buffered_frames(write_index, read_index, frame_bytes) == 0 {
            let sink_frames = wire::usec_to_frames(latency.sink_usec, rate_hz);
            stream.empty_since = Some((seen, sink_frames));
        }
        let frames = wire::delay_frames(
            write_index,
            read_index,
            frame_bytes,
            latency.sink_usec,
            rate_hz,
        );
        Ok(json!({"frames": frames}))
    }

    fn drain(&mut self) -> Answer {
        let stream = self.stream()?;
        if !stream.playing {
            return Ok(json!({}));
        }
        let (channel, within) = (stream.channel, stream.buffer_time() + STREAM_GRACE);
        self.broken()?;
        self.conn
            .ack(
                Command::DrainPlaybackStream(channel),
                Instant::now() + within,
            )
            .map_err(|f| refuse("stream", format!("the buffer was not played out: {f}")))?;
        Ok(json!({}))
    }

    fn drop_buffer(&mut self) -> Answer {
        let channel = self.stream()?.channel;
        self.conn
            .ack(Command::FlushPlaybackStream(channel), deadline())
            .map_err(server)?;
        Ok(json!({}))
    }

    fn close(&mut self) -> Answer {
        let channel = self.stream()?.channel;
        self.conn.stream = None;
        self.conn
            .ack(Command::DeletePlaybackStream(channel), deadline())
            .map_err(server)?;
        Ok(json!({}))
    }
}

/// Writes one answer line.
fn answer(out: &mut impl Write, answer: Answer) -> io::Result<()> {
    let mut line = match answer {
        Ok(Value::Object(mut fields)) => {
            fields.insert("ok".to_string(), Value::Bool(true));
            Value::Object(fields)
        }
        Ok(other) => unreachable!("every answer is an object, not {other}"),
        Err(Refusal(error, detail)) => json!({"ok": false, "error": error, "detail": detail}),
    }
    .to_string();
    line.push('\n');
    out.write_all(line.as_bytes())?;
    out.flush()
}

/// The protocol's cookie length. A server refuses an authentication whose
/// cookie is not this long, whether or not it checks the cookie's contents.
const COOKIE_BYTES: usize = 256;

/// The cookie at `<home>/.config/pulse/cookie`: a zeroed cookie when `home` is
/// None or the file does not exist, as PulseAudio's own client library sends,
/// an error naming the path when it cannot be read.
fn cookie(home: Option<OsString>) -> Result<Vec<u8>, String> {
    let Some(home) = home else {
        return Ok(vec![0; COOKIE_BYTES]);
    };
    let path = PathBuf::from(home).join(".config/pulse/cookie");
    match std::fs::read(&path) {
        Ok(cookie) => Ok(cookie),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(vec![0; COOKIE_BYTES]),
        Err(e) => Err(format!("cannot read the cookie at {}: {e}", path.display())),
    }
}

fn serve(client: &mut Client, input: &mut impl BufRead, out: &mut impl Write) -> io::Result<()> {
    let mut line = String::new();
    loop {
        line.clear();
        if input.read_line(&mut line)? == 0 {
            if client.conn.stream.is_some() {
                let _ = client.close();
            }
            return Ok(());
        }
        let request: Request = match serde_json::from_str(line.trim_end()) {
            Ok(request) => request,
            Err(e) => {
                answer(
                    out,
                    Err(refuse("request", format!("unreadable request: {e}"))),
                )?;
                continue;
            }
        };
        let result = match request {
            Request::Sinks => client.sinks(),
            Request::Sink { name } => client.sink(&name),
            Request::SetSink {
                name,
                volumes,
                muted,
            } => client.set_sink(&name, volumes, muted),
            Request::Open {
                sink,
                rate_hz,
                channels,
                sample_format,
                period_frames,
                buffer_frames,
            } => client.open(
                &sink,
                rate_hz,
                channels,
                &sample_format,
                period_frames,
                buffer_frames,
            ),
            Request::Stream => client.stream_state(),
            Request::Write { bytes } => {
                let mut data = vec![0u8; bytes];
                input.read_exact(&mut data)?;
                client.write(&data)
            }
            Request::Delay => client.delay(),
            Request::Drain => client.drain(),
            Request::Drop => client.drop_buffer(),
            Request::Close => client.close(),
        };
        answer(out, result)?;
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let socket = match args.as_slice() {
        [_, flag, path] if flag == "--socket" => PathBuf::from(path),
        _ => {
            eprintln!("usage: pulse-client --socket <path>");
            return ExitCode::from(2);
        }
    };
    let cookie = match cookie(std::env::var_os("HOME")) {
        Ok(cookie) => cookie,
        Err(e) => {
            eprintln!("pulse-client: {e}");
            return ExitCode::FAILURE;
        }
    };
    let mut out = io::stdout().lock();
    let by = deadline();
    let connected = Conn::connect(&socket, cookie, by).and_then(|mut conn| {
        let info: ServerInfo = conn.reply(Command::GetServerInfo, by)?;
        Ok((conn, info))
    });
    let (conn, info) = match connected {
        Ok(connected) => connected,
        Err(fail) => {
            let detail = format!("{}: {fail}", socket.display());
            let _ = answer(&mut out, Err(refuse("no_server", detail)));
            return ExitCode::FAILURE;
        }
    };
    let first = json!({
        "library": {"name": LIBRARY, "version": LIBRARY_VERSION},
        "server": {"name": text(&info.server_name), "version": text(&info.server_version)},
    });
    if answer(&mut out, Ok(first)).is_err() {
        return ExitCode::FAILURE;
    }
    let mut client = Client { conn };
    let mut input = BufReader::new(io::stdin().lock());
    match serve(&mut client, &mut input, &mut out) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("pulse-client: {e}");
            ExitCode::FAILURE
        }
    }
}
