//! A sound server for tests: it speaks the PulseAudio native protocol through
//! the same crate the sink client uses, serves the commands the client sends,
//! plays a stream by consuming its frames at the stream's rate times a speed
//! factor, and keeps every frame it was handed in a file. A test moves it with
//! instructions: change a sink or the stream, remove a sink, kill, move or
//! suspend the stream, stall the clock, report an underflow, or give the next
//! streams a volume or a rate of their own in place of the one they ask for.
//!
//! It reports an underflow only when told to, so a slow test is never failed
//! by one it did not ask for. A stream granted a pre-buffer holds a tail
//! shorter than one minimum request unplayed until a drain. Its state names
//! the frames each stream had taken when it was first uncorked.

use std::collections::{BTreeMap, HashMap};
use std::ffi::{CStr, CString};
use std::fs::{File, OpenOptions};
use std::io::{self, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use pulseaudio::protocol::{
    self, AuthReply, ChannelMap, ChannelPosition, ChannelVolume, Command, CommandReply,
    CreatePlaybackStreamReply, FormatEncoding, FormatInfo, GetSinkInfo, PlaybackLatency,
    PlaybackStreamMovedParams, PlaybackStreamParams, ProtocolError, PulseError, Request,
    SampleFormat, SampleSpec, ServerInfo, SetClientNameReply, SinkFlags, SinkInfo, SinkInputInfo,
    SinkState, StreamSuspendedParams, Underflow, Volume,
};
use serde::Deserialize;
use serde_json::{Value, json};

/// How often the clock plays the streams and asks for frames.
const TICK: Duration = Duration::from_millis(5);

/// The control channel's number in a packet's descriptor.
const CONTROL: u32 = u32::MAX;

/// The protocol's cookie length.
const COOKIE_BYTES: usize = 256;

/// How long a write to a client may block.
const WRITE_TIMEOUT: Duration = Duration::from_secs(5);

/// The protocol's 0 dB, raw.
fn norm() -> u32 {
    Volume::NORM.as_u32()
}

fn yes() -> bool {
    true
}

/// A sink the stand-in serves, as a test describes it.
#[derive(Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct SinkSpec {
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
    pub rate_hz: u32,
    pub channels: u8,
    /// The server's name for the sink's format, e.g. `s32le`.
    pub format: String,
    /// The server's names for the channel positions; mono for one channel
    /// and front-left, front-right for two when absent.
    #[serde(default)]
    pub positions: Option<Vec<String>>,
    /// Raw volume per channel; 0 dB on every channel when absent.
    #[serde(default)]
    pub volumes: Option<Vec<u32>>,
    #[serde(default = "norm")]
    pub base_volume: u32,
    #[serde(default)]
    pub muted: bool,
    #[serde(default = "yes")]
    pub decibel_volume: bool,
    #[serde(default)]
    pub flat_volume: bool,
    #[serde(default)]
    pub hardware_volume: bool,
    /// The sink's latency, which the latency reply states.
    #[serde(default)]
    pub latency_usec: u64,
}

/// What a test tells the stand-in to do.
#[derive(Deserialize, Debug)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Instruction {
    /// Set a sink's raw volumes, as another program on the desktop would.
    SinkVolume { sink: String, volumes: Vec<u32> },
    /// Mute or unmute a sink.
    SinkMute { sink: String, muted: bool },
    /// Set every open stream's raw volumes.
    StreamVolume { volumes: Vec<u32> },
    /// Remove a sink; every stream on it is killed.
    RemoveSink { sink: String },
    /// Kill every open stream.
    KillStream,
    /// Move every open stream to another sink.
    MoveStream { sink: String },
    /// Suspend every open stream.
    SuspendStream,
    /// Stop or restart the clock: a stalled stand-in plays nothing and asks
    /// for nothing.
    Stall { stalled: bool },
    /// Report an underflow on every open stream.
    Underflow,
    /// Give every stream made from now on this raw volume per channel,
    /// whatever volume it asks for at creation or sets after.
    IgnoreInitialVolume { volume: u32 },
    /// Grant every stream made from now on this rate, whatever rate it asks
    /// for.
    GrantRate { rate_hz: u32 },
    /// From now on, give every stream made the raw volumes the last stream
    /// made was given, channel by channel and 0 on a channel the last had
    /// not, as a session manager restores a stream's volumes by the client's
    /// application name. A stream whose properties set `state.restore-props`
    /// to `false` keeps the volume it asks for and is not remembered. No
    /// stream is moved to a remembered sink, whatever its
    /// `state.restore-target`.
    RestoreStreams,
    /// From now on, refuse every client's set of a stream's volume.
    RefuseStreamVolume,
    /// Answer what the stand-in holds.
    State,
}

/// What the stand-in is started with.
pub struct Config {
    pub socket: PathBuf,
    pub sinks: Vec<SinkSpec>,
    /// How many times faster than real time the streams are played.
    pub speed: f64,
    /// The file every frame handed to the stand-in is appended to.
    pub frames: PathBuf,
}

struct Client {
    writer: UnixStream,
    version: u16,
    authenticated: bool,
    named: bool,
}

struct Stream {
    client: u32,
    sink_index: u32,
    spec: SampleSpec,
    map: ChannelMap,
    volume: ChannelVolume,
    /// How many times a client has set the stream's volume.
    volume_sets: u32,
    /// Whether a session manager may restore the stream's volumes and
    /// remember them: true unless its properties set `state.restore-props`
    /// to `false`.
    restore_props: bool,
    /// Whether a session manager may move the stream to a remembered sink:
    /// true unless its properties set `state.restore-target` to `false`.
    restore_target: bool,
    muted: bool,
    corked: bool,
    target: u64,
    minreq: u64,
    /// The pre-buffer granted, in bytes. With one, a buffer holding less
    /// than one minimum request plays nothing until a drain, as PipeWire's
    /// pulse server does.
    prebuf: u64,
    /// The bytes taken when the stream was first uncorked.
    taken_at_uncork: Option<u64>,
    /// Bytes asked for and not yet handed over.
    requested: u64,
    write_index: u64,
    read_index: u64,
    /// The sequence number of a drain to acknowledge once the buffer is
    /// played out.
    draining: Option<u32>,
    /// Bytes due to be played and not yet a whole frame.
    carry: f64,
}

impl Stream {
    fn frame_bytes(&self) -> u64 {
        (self.spec.format.bytes_per_sample() * usize::from(self.spec.channels)) as u64
    }

    fn buffered(&self) -> u64 {
        self.write_index - self.read_index
    }
}

struct State {
    sinks: Vec<SinkInfo>,
    clients: HashMap<u32, Client>,
    streams: BTreeMap<u32, Stream>,
    next_index: u32,
    speed: f64,
    stalled: bool,
    forced_volume: Option<u32>,
    forced_rate: Option<u32>,
    restoring: bool,
    refusing_stream_volume: bool,
    remembered: Option<Vec<u32>>,
    streams_made: u32,
    frames: File,
}

/// A running stand-in. Its threads run until the process ends.
pub struct StandIn {
    state: Arc<Mutex<State>>,
}

fn format_of(name: &str) -> Result<SampleFormat, String> {
    match name {
        "u8" => Ok(SampleFormat::U8),
        "s16le" => Ok(SampleFormat::S16Le),
        "s24le" => Ok(SampleFormat::S24Le),
        "s24-32le" => Ok(SampleFormat::S24In32Le),
        "s32le" => Ok(SampleFormat::S32Le),
        "float32le" => Ok(SampleFormat::Float32Le),
        _ => Err(format!("the stand-in knows no format {name}")),
    }
}

/// The positions the stand-in knows, by the server's names for them.
const POSITIONS: [(&str, ChannelPosition); 11] = [
    ("mono", ChannelPosition::Mono),
    ("front-left", ChannelPosition::FrontLeft),
    ("front-right", ChannelPosition::FrontRight),
    ("front-center", ChannelPosition::FrontCenter),
    ("rear-left", ChannelPosition::RearLeft),
    ("rear-right", ChannelPosition::RearRight),
    ("lfe", ChannelPosition::Lfe),
    ("side-left", ChannelPosition::SideLeft),
    ("side-right", ChannelPosition::SideRight),
    ("aux0", ChannelPosition::Aux0),
    ("aux1", ChannelPosition::Aux1),
];

fn position_of(name: &str) -> Result<ChannelPosition, String> {
    POSITIONS
        .iter()
        .find(|(n, _)| *n == name)
        .map(|&(_, p)| p)
        .ok_or_else(|| format!("the stand-in knows no position {name}"))
}

/// A position by the server's name. A position the stand-in has no name for
/// panics rather than be reported under a second spelling.
fn name_of(position: ChannelPosition) -> &'static str {
    POSITIONS
        .iter()
        .find(|&&(_, p)| p == position)
        .map(|&(n, _)| n)
        .unwrap_or_else(|| panic!("the stand-in has no name for position {position:?}"))
}

fn c_string(s: &str) -> Result<CString, String> {
    CString::new(s).map_err(|_| format!("{s:?} holds a NUL byte"))
}

fn volumes(raw: &[u32]) -> ChannelVolume {
    let mut volume = ChannelVolume::empty();
    for &v in raw {
        volume.push(Volume::from_u32_clamped(v));
    }
    volume
}

fn raw(volume: &ChannelVolume) -> Vec<u32> {
    volume.channels().iter().map(Volume::as_u32).collect()
}

fn sink_info(index: u32, spec: &SinkSpec) -> Result<SinkInfo, String> {
    let n = usize::from(spec.channels);
    let names = match (&spec.positions, n) {
        (Some(names), _) => names.clone(),
        (None, 1) => vec!["mono".to_string()],
        (None, 2) => vec!["front-left".to_string(), "front-right".to_string()],
        (None, _) => return Err(format!("{}: name its {n} positions", spec.name)),
    };
    if names.len() != n {
        return Err(format!(
            "{}: {} positions for {n} channels",
            spec.name,
            names.len()
        ));
    }
    let positions = names
        .iter()
        .map(|p| position_of(p))
        .collect::<Result<Vec<_>, _>>()?;
    let cvolume = match &spec.volumes {
        Some(v) if v.len() == n => volumes(v),
        Some(v) => {
            return Err(format!(
                "{}: {} volumes for {n} channels",
                spec.name,
                v.len()
            ));
        }
        None => ChannelVolume::norm(spec.channels),
    };
    let mut flags = SinkFlags::LATENCY;
    flags.set(SinkFlags::DECIBEL_VOLUME, spec.decibel_volume);
    flags.set(SinkFlags::FLAT_VOLUME, spec.flat_volume);
    flags.set(SinkFlags::HW_VOLUME_CTRL, spec.hardware_volume);
    Ok(SinkInfo {
        index,
        name: c_string(&spec.name)?,
        description: spec.description.as_deref().map(c_string).transpose()?,
        state: SinkState::Idle,
        sample_spec: SampleSpec {
            format: format_of(&spec.format)?,
            channels: spec.channels,
            sample_rate: spec.rate_hz,
        },
        channel_map: ChannelMap::new(positions),
        cvolume,
        muted: spec.muted,
        flags,
        actual_latency: spec.latency_usec,
        configured_latency: spec.latency_usec,
        base_volume: Volume::from_u32_clamped(spec.base_volume),
        formats: vec![FormatInfo::new(FormatEncoding::Pcm)],
        ..Default::default()
    })
}

impl State {
    fn sink_by_name(&self, name: &str) -> Option<usize> {
        self.sinks
            .iter()
            .position(|s| s.name.to_bytes() == name.as_bytes())
    }

    fn write(&mut self, client: u32, packet: &[u8]) {
        if let Some(c) = self.clients.get_mut(&client) {
            // A client that has gone away is dropped by its reading thread.
            let _ = c.writer.write_all(packet);
        }
    }

    fn version(&self, client: u32) -> u16 {
        self.clients
            .get(&client)
            .map_or(protocol::MAX_VERSION, |c| c.version)
    }

    fn reply<T: CommandReply>(&mut self, client: u32, seq: u32, reply: &T) {
        let mut buf = Vec::new();
        let version = self.version(client);
        if protocol::write_reply_message(&mut buf, seq, reply, version).is_ok() {
            self.write(client, &buf);
        }
    }

    fn ack(&mut self, client: u32, seq: u32) {
        let mut buf = Vec::new();
        if protocol::write_ack_message(&mut buf, seq).is_ok() {
            self.write(client, &buf);
        }
    }

    fn error(&mut self, client: u32, seq: u32, error: PulseError) {
        let mut buf = Vec::new();
        if protocol::write_error(&mut buf, seq, &error).is_ok() {
            self.write(client, &buf);
        }
    }

    fn event(&mut self, client: u32, event: &Command) {
        let mut buf = Vec::new();
        let version = self.version(client);
        if protocol::write_command_message(&mut buf, CONTROL, event, version).is_ok() {
            self.write(client, &buf);
        }
    }

    fn command(&mut self, client: u32, seq: u32, command: Command) {
        let Some(c) = self.clients.get_mut(&client) else {
            return;
        };
        if !c.authenticated {
            let Command::Auth(params) = command else {
                return self.error(client, seq, PulseError::AccessDenied);
            };
            // A server refuses a cookie of any other length, as PipeWire's does.
            if params.cookie.len() != COOKIE_BYTES {
                return self.error(client, seq, PulseError::Invalid);
            }
            c.authenticated = true;
            c.version = params.version.min(protocol::MAX_VERSION);
            let version = c.version;
            let reply = AuthReply {
                version,
                ..Default::default()
            };
            return self.reply(client, seq, &reply);
        }
        if !c.named {
            let Command::SetClientName(_) = command else {
                return self.error(client, seq, PulseError::Protocol);
            };
            c.named = true;
            return self.reply(client, seq, &SetClientNameReply { client_id: client });
        }
        match command {
            Command::GetServerInfo => {
                let info = ServerInfo {
                    server_name: Some(c"pulse-stand-in".to_owned()),
                    server_version: Some(c"0".to_owned()),
                    default_sink_name: self.sinks.first().map(|s| s.name.clone()),
                    ..Default::default()
                };
                self.reply(client, seq, &info);
            }
            Command::GetSinkInfoList => {
                let sinks = self.sinks.clone();
                self.reply(client, seq, &sinks);
            }
            Command::GetSinkInfo(query) => self.get_sink(client, seq, &query),
            Command::SetSinkVolume(params) => {
                let found = params
                    .device_name
                    .and_then(|n| self.sink_by_name(&n.to_string_lossy()));
                match found {
                    Some(i)
                        if params.volume.channels().len()
                            == self.sinks[i].cvolume.channels().len() =>
                    {
                        self.sinks[i].cvolume = params.volume;
                        self.ack(client, seq);
                    }
                    Some(_) => self.error(client, seq, PulseError::Invalid),
                    None => self.error(client, seq, PulseError::NoEntity),
                }
            }
            Command::SetSinkInputVolume(params) => {
                let forced = self.forced_volume.is_some();
                if self.refusing_stream_volume {
                    return self.error(client, seq, PulseError::AccessDenied);
                }
                match self.streams.get_mut(&params.index) {
                    Some(s) if params.volume.channels().len() == usize::from(s.spec.channels) => {
                        if !forced {
                            s.volume = params.volume;
                        }
                        s.volume_sets += 1;
                        self.ack(client, seq);
                    }
                    Some(_) => self.error(client, seq, PulseError::Invalid),
                    None => self.error(client, seq, PulseError::NoEntity),
                }
            }
            Command::SetSinkMute(params) => {
                let found = params
                    .device_name
                    .and_then(|n| self.sink_by_name(&n.to_string_lossy()));
                match found {
                    Some(i) => {
                        self.sinks[i].muted = params.mute;
                        self.ack(client, seq);
                    }
                    None => self.error(client, seq, PulseError::NoEntity),
                }
            }
            Command::CreatePlaybackStream(params) => self.create(client, seq, params),
            Command::GetSinkInputInfo(index) => self.get_input(client, seq, index),
            Command::CorkPlaybackStream(params) => match self.streams.get_mut(&params.channel) {
                Some(s) => {
                    s.corked = params.cork;
                    if !s.corked && s.taken_at_uncork.is_none() {
                        s.taken_at_uncork = Some(s.write_index);
                    }
                    self.ack(client, seq);
                }
                None => self.error(client, seq, PulseError::NoEntity),
            },
            Command::GetPlaybackLatency(params) => {
                let Some(s) = self.streams.get(&params.channel) else {
                    return self.error(client, seq, PulseError::NoEntity);
                };
                let sink_usec = self
                    .sinks
                    .iter()
                    .find(|k| k.index == s.sink_index)
                    .map_or(0, |k| k.actual_latency);
                let latency = PlaybackLatency {
                    sink_usec,
                    source_usec: 0,
                    playing: !s.corked,
                    local_time: params.now,
                    remote_time: SystemTime::now(),
                    write_offset: s.write_index as i64,
                    read_offset: s.read_index as i64,
                    underrun_for: 0,
                    playing_for: 0,
                };
                self.reply(client, seq, &latency);
            }
            Command::DrainPlaybackStream(channel) => match self.streams.get_mut(&channel) {
                Some(s) if s.buffered() == 0 => self.ack(client, seq),
                Some(s) => s.draining = Some(seq),
                None => self.error(client, seq, PulseError::NoEntity),
            },
            Command::FlushPlaybackStream(channel) => match self.streams.get_mut(&channel) {
                Some(s) => {
                    s.read_index = s.write_index;
                    self.ack(client, seq);
                }
                None => self.error(client, seq, PulseError::NoEntity),
            },
            Command::DeletePlaybackStream(channel) => {
                if self.streams.remove(&channel).is_some() {
                    self.ack(client, seq);
                } else {
                    self.error(client, seq, PulseError::NoEntity);
                }
            }
            _ => self.error(client, seq, PulseError::NotImplemented),
        }
    }

    fn get_sink(&mut self, client: u32, seq: u32, query: &GetSinkInfo) {
        let found = match (&query.index, &query.name) {
            (Some(index), _) => self.sinks.iter().find(|s| s.index == *index),
            (None, Some(name)) => self
                .sink_by_name(&name.to_string_lossy())
                .map(|i| &self.sinks[i]),
            (None, None) => None,
        };
        match found.cloned() {
            Some(sink) => self.reply(client, seq, &sink),
            None => self.error(client, seq, PulseError::NoEntity),
        }
    }

    fn create(&mut self, client: u32, seq: u32, params: PlaybackStreamParams) {
        let name = params.sink_name.as_ref().map(|n| n.to_string_lossy());
        let Some(i) = name.and_then(|n| self.sink_by_name(&n)) else {
            return self.error(client, seq, PulseError::NoEntity);
        };
        let spec = params.sample_spec;
        if spec.channels == 0
            || params.channel_map.num_channels() != spec.channels
            || params.buffer_attr.target_length == u32::MAX
            || params.buffer_attr.minimum_request_length == u32::MAX
        {
            return self.error(client, seq, PulseError::Invalid);
        }
        let mut volume = match (self.forced_volume, params.cvolume) {
            (Some(v), _) => volumes(&vec![v; usize::from(spec.channels)]),
            (None, Some(v)) => v,
            (None, None) => ChannelVolume::norm(spec.channels),
        };
        let not_false = |key: &CStr| params.props.get_bytes(key) != Some(&b"false\0"[..]);
        let restore_props = not_false(c"state.restore-props");
        let restore_target = not_false(c"state.restore-target");
        if self.restoring && restore_props {
            if let Some(last) = &self.remembered {
                let n = usize::from(spec.channels);
                volume = volumes(
                    &(0..n)
                        .map(|c| last.get(c).copied().unwrap_or(0))
                        .collect::<Vec<_>>(),
                );
            }
            self.remembered = Some(raw(&volume));
        }
        let channel = self.next_index;
        self.next_index += 1;
        self.streams_made += 1;
        let sink = &self.sinks[i];
        let mut buffer_attr = params.buffer_attr;
        // The server's default pre-buffer is the target length.
        if buffer_attr.pre_buffering == u32::MAX {
            buffer_attr.pre_buffering = buffer_attr.target_length;
        }
        let mut spec = spec;
        if let Some(rate_hz) = self.forced_rate {
            spec.sample_rate = rate_hz;
        }
        let reply = CreatePlaybackStreamReply {
            channel,
            stream_index: channel,
            requested_bytes: buffer_attr.target_length,
            buffer_attr,
            sample_spec: spec,
            channel_map: params.channel_map,
            sink_index: sink.index,
            sink_name: Some(sink.name.clone()),
            format: FormatInfo::new(FormatEncoding::Pcm),
            ..Default::default()
        };
        let stream = Stream {
            client,
            sink_index: sink.index,
            spec,
            map: params.channel_map,
            volume,
            volume_sets: 0,
            restore_props,
            restore_target,
            muted: params.flags.start_muted.unwrap_or(false),
            corked: params.flags.start_corked,
            target: u64::from(buffer_attr.target_length),
            minreq: u64::from(buffer_attr.minimum_request_length),
            prebuf: u64::from(buffer_attr.pre_buffering),
            taken_at_uncork: None,
            requested: u64::from(buffer_attr.target_length),
            write_index: 0,
            read_index: 0,
            draining: None,
            carry: 0.0,
        };
        self.streams.insert(channel, stream);
        self.reply(client, seq, &reply);
    }

    fn get_input(&mut self, client: u32, seq: u32, index: u32) {
        let Some(s) = self.streams.get(&index) else {
            return self.error(client, seq, PulseError::NoEntity);
        };
        let info = SinkInputInfo {
            index,
            name: c"brenn-lab play".to_owned(),
            client_index: Some(s.client),
            sink_index: s.sink_index,
            sample_spec: s.spec,
            channel_map: s.map,
            cvolume: s.volume,
            muted: s.muted,
            corked: s.corked,
            has_volume: true,
            volume_writable: true,
            format: FormatInfo::new(FormatEncoding::Pcm),
            ..Default::default()
        };
        self.reply(client, seq, &info);
    }

    /// Takes the frames a client handed over on a stream's channel. A frame
    /// it cannot append to the frames file is an error, which ends the
    /// connection.
    fn frames(&mut self, channel: u32, data: &[u8]) -> io::Result<()> {
        let Some(s) = self.streams.get_mut(&channel) else {
            return Ok(());
        };
        s.write_index += data.len() as u64;
        s.requested = s.requested.saturating_sub(data.len() as u64);
        self.frames.write_all(data).inspect_err(|e| {
            eprintln!("pulse-stand-in: cannot keep the frames it took: {e}");
        })
    }

    /// Plays every uncorked stream for `elapsed`, acknowledges the drains
    /// that finished and asks for the frames each buffer lacks.
    fn tick(&mut self, elapsed: Duration) {
        if self.stalled {
            return;
        }
        let mut drained = Vec::new();
        let mut asks = Vec::new();
        for (&channel, s) in &mut self.streams {
            if s.corked {
                continue;
            }
            let frame = s.frame_bytes();
            let rate = f64::from(s.spec.sample_rate) * frame as f64 * self.speed;
            s.carry += rate * elapsed.as_secs_f64();
            let due = (s.carry as u64) / frame * frame;
            s.carry -= due as f64;
            let held = s.prebuf > 0 && s.buffered() < s.minreq && s.draining.is_none();
            let played = if held { 0 } else { due.min(s.buffered()) };
            s.read_index += played;
            if s.buffered() == 0
                && let Some(seq) = s.draining.take()
            {
                drained.push((s.client, seq));
            }
            let lacking = s.target.saturating_sub(s.buffered() + s.requested);
            if lacking > 0 && lacking >= s.minreq {
                s.requested += lacking;
                asks.push((s.client, channel, lacking));
            }
        }
        for (client, seq) in drained {
            self.ack(client, seq);
        }
        for (client, channel, length) in asks {
            let length = u32::try_from(length).unwrap_or(u32::MAX);
            self.event(client, &Command::Request(Request { channel, length }));
        }
    }

    fn open_streams(&self) -> Vec<(u32, u32)> {
        self.streams.iter().map(|(&c, s)| (s.client, c)).collect()
    }

    fn instruct(&mut self, instruction: Instruction) -> Result<Value, String> {
        let sink_at = |state: &State, name: &str| {
            state
                .sink_by_name(name)
                .ok_or_else(|| format!("no sink is named {name}"))
        };
        match instruction {
            Instruction::SinkVolume { sink, volumes: v } => {
                let i = sink_at(self, &sink)?;
                if v.len() != self.sinks[i].cvolume.channels().len() {
                    return Err(format!("{sink}: {} volumes for its channels", v.len()));
                }
                self.sinks[i].cvolume = volumes(&v);
            }
            Instruction::SinkMute { sink, muted } => {
                let i = sink_at(self, &sink)?;
                self.sinks[i].muted = muted;
            }
            Instruction::StreamVolume { volumes: v } => {
                for s in self.streams.values_mut() {
                    s.volume = volumes(&v);
                }
            }
            Instruction::RemoveSink { sink } => {
                let i = sink_at(self, &sink)?;
                let index = self.sinks.remove(i).index;
                let on: Vec<u32> = self
                    .streams
                    .iter()
                    .filter(|(_, s)| s.sink_index == index)
                    .map(|(&c, _)| c)
                    .collect();
                for channel in on {
                    self.kill(channel);
                }
            }
            Instruction::KillStream => {
                for (_, channel) in self.open_streams() {
                    self.kill(channel);
                }
            }
            Instruction::MoveStream { sink } => {
                let i = sink_at(self, &sink)?;
                let (index, name) = (self.sinks[i].index, self.sinks[i].name.clone());
                for (client, channel) in self.open_streams() {
                    let Some(s) = self.streams.get_mut(&channel) else {
                        continue;
                    };
                    s.sink_index = index;
                    let moved = PlaybackStreamMovedParams {
                        stream_index: channel,
                        device_index: index,
                        device_name: name.clone(),
                        device_suspended: false,
                        buffer_attr: protocol::stream::BufferAttr {
                            max_length: u32::MAX,
                            target_length: u32::try_from(s.target).unwrap_or(u32::MAX),
                            pre_buffering: u32::try_from(s.prebuf).unwrap_or(u32::MAX),
                            minimum_request_length: u32::try_from(s.minreq).unwrap_or(u32::MAX),
                            fragment_size: u32::MAX,
                        },
                        configured_sink_latency: 0,
                    };
                    self.event(client, &Command::PlaybackStreamMoved(moved));
                }
            }
            Instruction::SuspendStream => {
                for (client, channel) in self.open_streams() {
                    let suspended = StreamSuspendedParams {
                        stream_index: channel,
                        suspended: true,
                    };
                    self.event(client, &Command::PlaybackStreamSuspended(suspended));
                }
            }
            Instruction::Stall { stalled } => self.stalled = stalled,
            Instruction::Underflow => {
                for (client, channel) in self.open_streams() {
                    let offset = self.streams.get(&channel).map_or(0, |s| s.read_index);
                    let underflow = Underflow {
                        channel,
                        offset: offset as i64,
                    };
                    self.event(client, &Command::Underflow(underflow));
                }
            }
            Instruction::IgnoreInitialVolume { volume } => self.forced_volume = Some(volume),
            Instruction::GrantRate { rate_hz } => self.forced_rate = Some(rate_hz),
            Instruction::RestoreStreams => self.restoring = true,
            Instruction::RefuseStreamVolume => self.refusing_stream_volume = true,
            Instruction::State => return Ok(self.snapshot()),
        }
        Ok(json!({}))
    }

    fn kill(&mut self, channel: u32) {
        if let Some(s) = self.streams.remove(&channel) {
            self.event(s.client, &Command::PlaybackStreamKilled(channel));
        }
    }

    fn snapshot(&self) -> Value {
        let sinks: Vec<Value> = self
            .sinks
            .iter()
            .map(|s| {
                json!({
                    "name": s.name.to_string_lossy(),
                    "volumes": raw(&s.cvolume),
                    "muted": s.muted,
                })
            })
            .collect();
        let streams: Vec<Value> = self
            .streams
            .iter()
            .map(|(&channel, s)| {
                let sink = self.sinks.iter().find(|k| k.index == s.sink_index);
                json!({
                    "channel": channel,
                    "sink": sink.map(|k| k.name.to_string_lossy()),
                    "positions": (&s.map).into_iter().map(name_of).collect::<Vec<_>>(),
                    "volumes": raw(&s.volume),
                    "volume_sets": s.volume_sets,
                    "restore_props": s.restore_props,
                    "restore_target": s.restore_target,
                    "muted": s.muted,
                    "corked": s.corked,
                    "taken_frames": s.write_index / s.frame_bytes(),
                    "taken_frames_at_uncork": s.taken_at_uncork.map(|b| b / s.frame_bytes()),
                    "played_frames": s.read_index / s.frame_bytes(),
                })
            })
            .collect();
        json!({"sinks": sinks, "streams": streams, "streams_made": self.streams_made})
    }
}

impl StandIn {
    /// Binds `config.socket` and starts serving.
    pub fn start(config: Config) -> Result<StandIn, String> {
        if !(config.speed.is_finite() && config.speed > 0.0) {
            return Err(format!("speed {} is no positive number", config.speed));
        }
        let mut sinks = Vec::new();
        for (index, spec) in config.sinks.iter().enumerate() {
            let index = u32::try_from(index).map_err(|e| e.to_string())?;
            sinks.push(sink_info(index, spec)?);
        }
        let frames = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&config.frames)
            .map_err(|e| format!("{}: {e}", config.frames.display()))?;
        let listener = UnixListener::bind(&config.socket)
            .map_err(|e| format!("{}: {e}", config.socket.display()))?;
        let state = Arc::new(Mutex::new(State {
            sinks,
            clients: HashMap::new(),
            streams: BTreeMap::new(),
            next_index: 0,
            speed: config.speed,
            stalled: false,
            forced_volume: None,
            forced_rate: None,
            restoring: false,
            refusing_stream_volume: false,
            remembered: None,
            streams_made: 0,
            frames,
        }));
        let accepting = Arc::clone(&state);
        thread::spawn(move || accept(&listener, &accepting));
        let ticking = Arc::clone(&state);
        thread::spawn(move || clock(&ticking));
        Ok(StandIn { state })
    }

    /// Carries out one instruction.
    pub fn instruct(&self, instruction: Instruction) -> Result<Value, String> {
        lock(&self.state).instruct(instruction)
    }
}

fn lock(state: &Mutex<State>) -> MutexGuard<'_, State> {
    // A thread that panicked holding the lock left the state as it was; the
    // stand-in serves on.
    state
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn clock(state: &Mutex<State>) {
    let mut last = Instant::now();
    loop {
        thread::sleep(TICK);
        let now = Instant::now();
        lock(state).tick(now - last);
        last = now;
    }
}

fn accept(listener: &UnixListener, state: &Arc<Mutex<State>>) {
    let mut next_client = 0u32;
    for sock in listener.incoming() {
        let Ok(sock) = sock else { continue };
        let Ok(writer) = sock.try_clone() else {
            continue;
        };
        if writer.set_write_timeout(Some(WRITE_TIMEOUT)).is_err() {
            continue;
        }
        let client = next_client;
        next_client += 1;
        lock(state).clients.insert(
            client,
            Client {
                writer,
                version: protocol::MAX_VERSION,
                authenticated: false,
                named: false,
            },
        );
        let serving = Arc::clone(state);
        thread::spawn(move || {
            // The connection's end, by close or by error, ends the client.
            let _ = serve(client, sock, &serving);
            let mut state = lock(&serving);
            state.clients.remove(&client);
            state.streams.retain(|_, s| s.client != client);
        });
    }
}

fn serve(client: u32, sock: UnixStream, state: &Mutex<State>) -> Result<(), ProtocolError> {
    let mut reader = BufReader::new(sock);
    let mut payload = Vec::new();
    loop {
        let desc = protocol::read_descriptor(&mut reader)?;
        payload.resize(desc.length as usize, 0);
        reader.read_exact(&mut payload)?;
        let mut state = lock(state);
        if desc.channel != CONTROL {
            state.frames(desc.channel, &payload)?;
            continue;
        }
        let version = state.version(client);
        match Command::read_tag_prefixed(&mut io::Cursor::new(&payload), version) {
            Ok((seq, command)) => state.command(client, seq, command),
            Err(_) => state.error(client, CONTROL, PulseError::Protocol),
        }
    }
}
