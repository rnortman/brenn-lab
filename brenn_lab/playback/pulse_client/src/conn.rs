//! One connection to the sound server over a blocking Unix socket: the
//! handshake, requests matched to their replies by sequence number, and the
//! one open stream: what the server granted and what it has said about the
//! stream since, unasked.

use std::ffi::CString;
use std::fmt;
use std::io::{self, Cursor, ErrorKind, Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::{Duration, Instant};

use pulseaudio::protocol::{
    self, AuthParams, AuthReply, Command, CommandReply, CreatePlaybackStreamReply, DESCRIPTOR_SIZE,
    Descriptor, Prop, Props, ProtocolError, PulseError, SampleSpec, SetClientNameReply,
    TagStructReader,
};

use crate::wire;

/// How long one request may take, every round trip to the server it makes
/// counted against one deadline; the handshake and the server's info are one
/// request. A write's wait for credit and a drain have their own bounds.
/// Shorter than the binding's bound on any answer, `REPLY_TIMEOUT_S` in
/// `pulse.py`, so a server that answers nothing reaches the binding as this
/// client's refusal and not as a client that went silent.
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(4);

/// The control channel's number in a packet's descriptor.
const CONTROL: u32 = u32::MAX;

/// The most bytes one read from the socket takes.
const READ_BYTES: usize = 64 * 1024;

/// Why a request got no reply it could use.
#[derive(Debug)]
pub enum Fail {
    /// The server answered with an error.
    Server(PulseError),
    /// No reply within the request's bound.
    Timeout,
    /// The server closed the connection.
    Closed,
    /// A message that could not be read, or a socket error.
    Wire(String),
}

impl fmt::Display for Fail {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Fail::Server(e) => write!(f, "the server refused: {e}"),
            Fail::Timeout => write!(f, "no reply from the server in time"),
            Fail::Closed => write!(f, "the server closed the connection"),
            Fail::Wire(detail) => write!(f, "{detail}"),
        }
    }
}

impl From<io::Error> for Fail {
    fn from(e: io::Error) -> Self {
        Fail::Wire(format!("socket: {e}"))
    }
}

impl From<ProtocolError> for Fail {
    fn from(e: ProtocolError) -> Self {
        match e {
            ProtocolError::ServerError(e) => Fail::Server(e),
            e => Fail::Wire(format!("protocol: {e}")),
        }
    }
}

/// The open stream: what the server granted, and what it has said about the
/// stream since, unasked.
#[derive(Debug)]
pub struct Stream {
    /// The stream's channel; events for any other are not the stream's.
    pub channel: u32,
    /// The stream's index among the server's sink inputs.
    pub index: u32,
    /// The sample spec the server granted.
    pub spec: SampleSpec,
    /// The buffer's target length the server granted, in bytes.
    pub buffer_bytes: u64,
    /// Bytes the server has asked for and not yet been sent.
    pub credit: u64,
    /// An underflow was reported and not yet answered.
    pub underflow: bool,
    /// Why the stream can no longer play, once the server said so.
    pub broken: Option<String>,
    /// Whether the stream has been uncorked. A suspension breaks only a
    /// stream that plays; a drain waits and the delay is asked of the server
    /// only on one.
    pub playing: bool,
    /// When the buffer was seen empty since the last write, and the sink's
    /// latency then, in frames.
    pub empty_since: Option<(Instant, u64)>,
}

impl Stream {
    /// The stream the server made, corked, with the credit it granted.
    pub fn new(reply: &CreatePlaybackStreamReply) -> Stream {
        Stream {
            channel: reply.channel,
            index: reply.stream_index,
            spec: reply.sample_spec,
            buffer_bytes: u64::from(reply.buffer_attr.target_length),
            credit: u64::from(reply.requested_bytes),
            underflow: false,
            broken: None,
            playing: false,
            empty_since: None,
        }
    }

    pub fn frame_bytes(&self) -> u64 {
        wire::frame_bytes(&self.spec)
    }

    /// How long the granted buffer takes to play.
    pub fn buffer_time(&self) -> Duration {
        self.spec.bytes_to_duration(self.buffer_bytes as usize)
    }
}

/// A connection after the handshake.
pub struct Conn {
    sock: UnixStream,
    inbuf: Vec<u8>,
    /// Where one read from the socket lands, kept between reads.
    chunk: Box<[u8]>,
    /// Where one outgoing packet is built, so that it goes out in one write.
    outbuf: Vec<u8>,
    version: u16,
    next_seq: u32,
    /// The open stream, if there is one.
    pub stream: Option<Stream>,
}

impl Conn {
    /// Connects to the socket at `path`, authenticates with `cookie` and
    /// names the client, all by `deadline`.
    pub fn connect(path: &Path, cookie: Vec<u8>, deadline: Instant) -> Result<Conn, Fail> {
        let sock = UnixStream::connect(path)?;
        let mut conn = Conn {
            sock,
            inbuf: Vec::new(),
            chunk: vec![0u8; READ_BYTES].into_boxed_slice(),
            outbuf: Vec::new(),
            version: protocol::MAX_VERSION,
            next_seq: 0,
            stream: None,
        };
        let auth: AuthReply = conn.reply(
            Command::Auth(AuthParams {
                version: protocol::MAX_VERSION,
                supports_shm: false,
                supports_memfd: false,
                cookie,
            }),
            deadline,
        )?;
        if auth.version < protocol::MIN_VERSION {
            return Err(Fail::Wire(format!(
                "the server speaks protocol {}, under the client's least, {}",
                auth.version,
                protocol::MIN_VERSION
            )));
        }
        conn.version = auth.version.min(protocol::MAX_VERSION);
        let mut props = Props::new();
        props.set(Prop::ApplicationName, c"brenn-lab pulse-client");
        let pid = CString::new(std::process::id().to_string())
            .map_err(|e| Fail::Wire(format!("process id: {e}")))?;
        props.set(Prop::ApplicationProcessId, pid);
        let _: SetClientNameReply = conn.reply(Command::SetClientName(props), deadline)?;
        Ok(conn)
    }

    /// Sends `command` and reads its reply's payload as `T`, by `deadline`.
    pub fn reply<T: CommandReply>(
        &mut self,
        command: Command,
        deadline: Instant,
    ) -> Result<T, Fail> {
        let payload = self.call(command, deadline)?;
        let mut cursor = Cursor::new(payload.as_slice());
        let mut ts = TagStructReader::new(&mut cursor, self.version);
        Ok(T::read(&mut ts, self.version)?)
    }

    /// Sends `command` and waits for its empty reply, by `deadline`.
    pub fn ack(&mut self, command: Command, deadline: Instant) -> Result<(), Fail> {
        self.call(command, deadline).map(|_| ())
    }

    /// Sends `command`, takes in every other message until its reply, and
    /// returns the reply's payload after its tag and sequence number.
    fn call(&mut self, command: Command, deadline: Instant) -> Result<Vec<u8>, Fail> {
        let seq = self.next_seq;
        self.next_seq = self.next_seq.wrapping_add(1) % CONTROL;
        self.outbuf.clear();
        protocol::write_command_message(&mut self.outbuf, seq, &command, self.version)?;
        self.put(deadline)?;
        loop {
            let Some((desc, payload)) = self.packet(deadline)? else {
                return Err(Fail::Timeout);
            };
            if desc.channel != CONTROL {
                continue;
            }
            let mut cursor = Cursor::new(payload.as_slice());
            let parsed = Command::read_tag_prefixed(&mut cursor, self.version);
            let (got, message) = match parsed {
                Ok(read) => read,
                // A message the crate does not read is no reply to wait for.
                Err(ProtocolError::Unimplemented(..)) => continue,
                Err(e) => return Err(e.into()),
            };
            match message {
                Command::Reply if got == seq => {
                    let at = usize::try_from(cursor.position())
                        .map_err(|e| Fail::Wire(format!("reply offset: {e}")))?;
                    return Ok(payload[at..].to_vec());
                }
                Command::Error(e) if got == seq => return Err(Fail::Server(e)),
                Command::Reply | Command::Error(_) => {}
                event => self.take(event),
            }
        }
    }

    /// Takes in the server's messages until `deadline` or until one arrives,
    /// whichever is first; false if none came. A deadline already past takes
    /// one message if one is buffered whole, and reads nothing.
    pub fn wait(&mut self, deadline: Instant) -> Result<bool, Fail> {
        let Some((desc, payload)) = self.packet(deadline)? else {
            return Ok(false);
        };
        if desc.channel == CONTROL {
            match Command::read_tag_prefixed(&mut Cursor::new(payload.as_slice()), self.version) {
                Ok((_, Command::Reply | Command::Error(_))) => {}
                Ok((_, event)) => self.take(event),
                Err(ProtocolError::Unimplemented(..)) => {}
                Err(e) => return Err(e.into()),
            }
        }
        Ok(true)
    }

    /// Takes in every message the server has already sent, without waiting.
    pub fn take_pending(&mut self) -> Result<(), Fail> {
        self.sock.set_nonblocking(true)?;
        let read = loop {
            match self.sock.read(&mut self.chunk) {
                Ok(0) => break Err(Fail::Closed),
                Ok(n) => self.inbuf.extend_from_slice(&self.chunk[..n]),
                Err(e) if e.kind() == ErrorKind::WouldBlock => break Ok(()),
                Err(e) if e.kind() == ErrorKind::Interrupted => {}
                Err(e) => break Err(Fail::from(e)),
            }
        };
        self.sock.set_nonblocking(false)?;
        read?;
        while self.wait(Instant::now())? {}
        Ok(())
    }

    /// Writes `block` to the stream on `channel` as one memory block, by
    /// `deadline`.
    pub fn send(&mut self, channel: u32, block: &[u8], deadline: Instant) -> Result<(), Fail> {
        self.outbuf.clear();
        protocol::write_memblock(&mut self.outbuf, channel, block, 0)?;
        self.put(deadline)
    }

    /// Writes the whole of `outbuf` to the socket by `deadline`.
    fn put(&mut self, deadline: Instant) -> Result<(), Fail> {
        let mut sent = 0;
        while sent < self.outbuf.len() {
            let now = Instant::now();
            if now >= deadline {
                return Err(Fail::Timeout);
            }
            let left = (deadline - now).max(Duration::from_millis(1));
            self.sock.set_write_timeout(Some(left))?;
            match self.sock.write(&self.outbuf[sent..]) {
                Ok(0) => return Err(Fail::Closed),
                Ok(n) => sent += n,
                Err(e)
                    if matches!(
                        e.kind(),
                        ErrorKind::WouldBlock | ErrorKind::TimedOut | ErrorKind::Interrupted
                    ) => {}
                Err(e) => return Err(e.into()),
            }
        }
        Ok(())
    }

    /// Records what an unsolicited message says about the stream.
    fn take(&mut self, event: Command) {
        let Some(stream) = self.stream.as_mut() else {
            return;
        };
        let channel = stream.channel;
        match event {
            Command::Request(r) if r.channel == channel => {
                stream.credit += u64::from(r.length);
            }
            Command::Underflow(u) if u.channel == channel => stream.underflow = true,
            Command::PlaybackStreamKilled(c) if c == channel => {
                stream.broken = Some("the server killed the stream".to_string());
            }
            Command::PlaybackStreamMoved(m) if m.stream_index == channel => {
                stream.broken = Some(format!(
                    "the server moved the stream to {}",
                    m.device_name.to_string_lossy()
                ));
            }
            Command::PlaybackStreamSuspended(s)
                if s.stream_index == channel && s.suspended && stream.playing =>
            {
                stream.broken = Some("the server suspended the stream".to_string());
            }
            _ => {}
        }
    }

    /// The next whole packet, or None if none is whole by `deadline`.
    fn packet(&mut self, deadline: Instant) -> Result<Option<(Descriptor, Vec<u8>)>, Fail> {
        loop {
            if self.inbuf.len() >= DESCRIPTOR_SIZE {
                let desc = protocol::read_descriptor(&mut Cursor::new(&self.inbuf[..]))?;
                let end = DESCRIPTOR_SIZE + desc.length as usize;
                if self.inbuf.len() >= end {
                    let payload = self.inbuf[DESCRIPTOR_SIZE..end].to_vec();
                    self.inbuf.drain(..end);
                    return Ok(Some((desc, payload)));
                }
            }
            let now = Instant::now();
            if now >= deadline {
                return Ok(None);
            }
            let left = (deadline - now).max(Duration::from_millis(1));
            self.sock.set_read_timeout(Some(left))?;
            match self.sock.read(&mut self.chunk) {
                Ok(0) => return Err(Fail::Closed),
                Ok(n) => self.inbuf.extend_from_slice(&self.chunk[..n]),
                Err(e) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {}
                Err(e) if e.kind() == ErrorKind::Interrupted => {}
                Err(e) => return Err(e.into()),
            }
        }
    }
}
