//! The client's pure functions: names the server's values go out under, the
//! stream's channel map, the sample formats it speaks, and the delay.

use std::time::Duration;

use ChannelPosition::{
    FrontCenter, FrontLeft, FrontRight, Lfe, Mono, RearLeft, RearRight, SideLeft, SideRight,
};
use pulseaudio::protocol::{ChannelMap, ChannelPosition, SampleFormat, SampleSpec};

/// The most channels the protocol carries in a stream.
pub const MAX_CHANNELS: usize = 32;

const AUX: [ChannelPosition; 32] = [
    ChannelPosition::Aux0,
    ChannelPosition::Aux1,
    ChannelPosition::Aux2,
    ChannelPosition::Aux3,
    ChannelPosition::Aux4,
    ChannelPosition::Aux5,
    ChannelPosition::Aux6,
    ChannelPosition::Aux7,
    ChannelPosition::Aux8,
    ChannelPosition::Aux9,
    ChannelPosition::Aux10,
    ChannelPosition::Aux11,
    ChannelPosition::Aux12,
    ChannelPosition::Aux13,
    ChannelPosition::Aux14,
    ChannelPosition::Aux15,
    ChannelPosition::Aux16,
    ChannelPosition::Aux17,
    ChannelPosition::Aux18,
    ChannelPosition::Aux19,
    ChannelPosition::Aux20,
    ChannelPosition::Aux21,
    ChannelPosition::Aux22,
    ChannelPosition::Aux23,
    ChannelPosition::Aux24,
    ChannelPosition::Aux25,
    ChannelPosition::Aux26,
    ChannelPosition::Aux27,
    ChannelPosition::Aux28,
    ChannelPosition::Aux29,
    ChannelPosition::Aux30,
    ChannelPosition::Aux31,
];

/// The positions of a stream of `channels` channels, in a WAV file's channel
/// order: the order of the `WAVEFORMATEXTENSIBLE` channel-mask bits for the
/// usual layout of each count that has one (stereo `0x3`, quad `0x33`, 5.0
/// with its pair labelled rear, 5.1 `0x3F`, 7.1 `0x63F`). A count with no
/// such layout takes the map of the largest count under it that has one,
/// then `aux0`, `aux1`, … for the rest. None for no channels or more than
/// the protocol carries.
pub fn stream_positions(channels: usize) -> Option<Vec<ChannelPosition>> {
    if channels == 0 || channels > MAX_CHANNELS {
        return None;
    }
    let mut positions = match channels {
        1 => vec![Mono],
        2..=3 => vec![FrontLeft, FrontRight],
        4 => vec![FrontLeft, FrontRight, RearLeft, RearRight],
        5 => vec![FrontLeft, FrontRight, FrontCenter, RearLeft, RearRight],
        6..=7 => vec![FrontLeft, FrontRight, FrontCenter, Lfe, RearLeft, RearRight],
        _ => vec![
            FrontLeft,
            FrontRight,
            FrontCenter,
            Lfe,
            RearLeft,
            RearRight,
            SideLeft,
            SideRight,
        ],
    };
    let named = positions.len();
    positions.extend_from_slice(&AUX[..channels - named]);
    Some(positions)
}

/// The channel map of `stream_positions`.
pub fn stream_map(channels: usize) -> Option<ChannelMap> {
    stream_positions(channels).map(ChannelMap::new)
}

/// A channel position by the server's own name for it.
pub fn position_name(position: ChannelPosition) -> String {
    const NAMED: [&str; 12] = [
        "mono",
        "front-left",
        "front-right",
        "front-center",
        "rear-center",
        "rear-left",
        "rear-right",
        "lfe",
        "front-left-of-center",
        "front-right-of-center",
        "side-left",
        "side-right",
    ];
    const TOP: [&str; 7] = [
        "top-center",
        "top-front-left",
        "top-front-right",
        "top-front-center",
        "top-rear-left",
        "top-rear-right",
        "top-rear-center",
    ];
    let n = position as usize;
    match n {
        0..=11 => NAMED[n].to_string(),
        12..=43 => format!("aux{}", n - 12),
        _ => TOP[n - 44].to_string(),
    }
}

/// A sample format by the server's own name for it, unparsed by the tree.
pub fn format_name(format: SampleFormat) -> &'static str {
    match format {
        SampleFormat::Invalid => "invalid",
        SampleFormat::U8 => "u8",
        SampleFormat::Alaw => "aLaw",
        SampleFormat::Ulaw => "uLaw",
        SampleFormat::S16Le => "s16le",
        SampleFormat::S16Be => "s16be",
        SampleFormat::Float32Le => "float32le",
        SampleFormat::Float32Be => "float32be",
        SampleFormat::S32Le => "s32le",
        SampleFormat::S32Be => "s32be",
        SampleFormat::S24Le => "s24le",
        SampleFormat::S24Be => "s24be",
        SampleFormat::S24In32Le => "s24-32le",
        SampleFormat::S24In32Be => "s24-32be",
    }
}

/// The protocol's format for one of the tree's sample formats; None for a
/// name the tree does not speak.
pub fn stream_format(sample_format: &str) -> Option<SampleFormat> {
    match sample_format {
        "s16_le" => Some(SampleFormat::S16Le),
        "s32_le" => Some(SampleFormat::S32Le),
        _ => None,
    }
}

/// The tree's name for a stream format; None for one it does not speak.
pub fn tree_format(format: SampleFormat) -> Option<&'static str> {
    match format {
        SampleFormat::S16Le => Some("s16_le"),
        SampleFormat::S32Le => Some("s32_le"),
        _ => None,
    }
}

/// The bytes in one frame of `spec`.
pub fn frame_bytes(spec: &SampleSpec) -> u64 {
    (spec.format.bytes_per_sample() * usize::from(spec.channels)) as u64
}

/// Microseconds as frames at `rate_hz`, rounded down.
pub fn usec_to_frames(usec: u64, rate_hz: u32) -> u64 {
    let frames = u128::from(usec) * u128::from(rate_hz) / 1_000_000;
    u64::try_from(frames).unwrap_or(u64::MAX)
}

/// The frames in the stream's buffer, write index minus read index floored
/// at 0, in whole frames.
pub fn buffered_frames(write_index: i64, read_index: i64, frame_bytes: u64) -> u64 {
    let bytes = write_index.saturating_sub(read_index).max(0).unsigned_abs();
    bytes / frame_bytes
}

/// The stream's delay by the protocol's account of a playback stream's
/// latency: the frames in its buffer plus the sink's latency.
pub fn delay_frames(
    write_index: i64,
    read_index: i64,
    frame_bytes: u64,
    sink_usec: u64,
    rate_hz: u32,
) -> u64 {
    buffered_frames(write_index, read_index, frame_bytes) + usec_to_frames(sink_usec, rate_hz)
}

/// The sink's latency, `sink_frames` when the buffer was seen empty,
/// counted down by `elapsed` in real time and floored at 0.
pub fn counted_down(sink_frames: u64, elapsed: Duration, rate_hz: u32) -> u64 {
    let gone = u128::from(rate_hz) * elapsed.as_micros() / 1_000_000;
    u64::try_from(u128::from(sink_frames).saturating_sub(gone)).unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(channels: usize) -> Vec<String> {
        stream_positions(channels)
            .expect("a map")
            .into_iter()
            .map(position_name)
            .collect()
    }

    #[test]
    fn maps_for_one_to_nine_channels() {
        let expected: [&[&str]; 9] = [
            &["mono"],
            &["front-left", "front-right"],
            &["front-left", "front-right", "aux0"],
            &["front-left", "front-right", "rear-left", "rear-right"],
            &[
                "front-left",
                "front-right",
                "front-center",
                "rear-left",
                "rear-right",
            ],
            &[
                "front-left",
                "front-right",
                "front-center",
                "lfe",
                "rear-left",
                "rear-right",
            ],
            &[
                "front-left",
                "front-right",
                "front-center",
                "lfe",
                "rear-left",
                "rear-right",
                "aux0",
            ],
            &[
                "front-left",
                "front-right",
                "front-center",
                "lfe",
                "rear-left",
                "rear-right",
                "side-left",
                "side-right",
            ],
            &[
                "front-left",
                "front-right",
                "front-center",
                "lfe",
                "rear-left",
                "rear-right",
                "side-left",
                "side-right",
                "aux0",
            ],
        ];
        for (i, want) in expected.iter().enumerate() {
            assert_eq!(names(i + 1), *want, "{} channels", i + 1);
        }
    }

    #[test]
    fn thirty_two_channels_end_in_aux23() {
        let got = names(32);
        assert_eq!(got.len(), 32);
        assert_eq!(got[..8], names(8)[..]);
        assert_eq!(got[2..4], ["front-center", "lfe"]);
        assert_eq!(got[8], "aux0");
        assert_eq!(got[31], "aux23");
        assert_eq!(stream_map(32).expect("a map").num_channels(), 32);
    }

    #[test]
    fn no_map_for_none_or_more_than_the_protocol_carries() {
        assert!(stream_positions(0).is_none());
        assert!(stream_positions(33).is_none());
        assert!(stream_map(33).is_none());
    }

    #[test]
    fn position_names_cover_every_position() {
        assert_eq!(position_name(ChannelPosition::Aux31), "aux31");
        assert_eq!(position_name(ChannelPosition::TopCenter), "top-center");
        assert_eq!(
            position_name(ChannelPosition::TopRearCenter),
            "top-rear-center"
        );
        assert_eq!(position_name(ChannelPosition::SideRight), "side-right");
    }

    #[test]
    fn formats_the_tree_speaks_and_no_other() {
        assert_eq!(stream_format("s16_le"), Some(SampleFormat::S16Le));
        assert_eq!(stream_format("s32_le"), Some(SampleFormat::S32Le));
        assert_eq!(stream_format("s24_3le"), None);
        assert_eq!(tree_format(SampleFormat::S32Le), Some("s32_le"));
        assert_eq!(tree_format(SampleFormat::Float32Le), None);
        assert_eq!(format_name(SampleFormat::Float32Le), "float32le");
        assert_eq!(format_name(SampleFormat::S24In32Le), "s24-32le");
    }

    #[test]
    fn delay_is_the_buffer_plus_the_sink_latency() {
        // 4 bytes a frame: 400 bytes buffered are 100 frames; 10 ms at 48 kHz
        // are 480.
        assert_eq!(delay_frames(1400, 1000, 4, 10_000, 48_000), 580);
        // A read index past the write index (an underrun) buffers nothing.
        assert_eq!(delay_frames(1000, 1400, 4, 10_000, 48_000), 480);
        // A partial frame is not counted.
        assert_eq!(delay_frames(1003, 1000, 4, 0, 48_000), 0);
    }

    #[test]
    fn the_sink_latency_counts_down_to_zero() {
        assert_eq!(counted_down(480, Duration::ZERO, 48_000), 480);
        assert_eq!(counted_down(480, Duration::from_millis(5), 48_000), 240);
        assert_eq!(counted_down(480, Duration::from_millis(10), 48_000), 0);
        assert_eq!(counted_down(480, Duration::from_secs(3), 48_000), 0);
    }

    #[test]
    fn microseconds_round_down_to_frames() {
        assert_eq!(usec_to_frames(1, 48_000), 0);
        assert_eq!(usec_to_frames(21, 48_000), 1);
        assert_eq!(usec_to_frames(1_000_000, 16_000), 16_000);
    }
}
