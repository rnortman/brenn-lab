//! pulse-stand-in: the test stand-in sound server as a program.
//!
//! `pulse-stand-in --socket <path> --sinks <json> --speed <factor> --frames
//! <path>` serves the sinks the JSON list describes at the socket, appends
//! every frame it is handed to the frames file, and answers `{"ready": true}`
//! on stdout once it accepts connections. It then reads one instruction a
//! line on stdin and answers each with one JSON line, carrying `ok`; it exits
//! when stdin closes, removing the socket.

use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use pulse_stand_in::{Config, Instruction, SinkSpec, StandIn};
use serde_json::{Value, json};

fn config(args: &[String]) -> Result<Config, String> {
    let mut socket = None;
    let mut sinks = None;
    let mut speed = None;
    let mut frames = None;
    let mut rest = args.iter();
    while let Some(flag) = rest.next() {
        let value = rest.next().ok_or_else(|| format!("{flag} takes a value"))?;
        match flag.as_str() {
            "--socket" => socket = Some(PathBuf::from(value)),
            "--sinks" => {
                let list: Vec<SinkSpec> =
                    serde_json::from_str(value).map_err(|e| format!("--sinks: {e}"))?;
                sinks = Some(list);
            }
            "--speed" => {
                speed = Some(value.parse().map_err(|e| format!("--speed: {e}"))?);
            }
            "--frames" => frames = Some(PathBuf::from(value)),
            _ => return Err(format!("unknown argument {flag}")),
        }
    }
    Ok(Config {
        socket: socket.ok_or("--socket is required")?,
        sinks: sinks.ok_or("--sinks is required")?,
        speed: speed.ok_or("--speed is required")?,
        frames: frames.ok_or("--frames is required")?,
    })
}

fn say(out: &mut impl Write, line: &Value) -> io::Result<()> {
    writeln!(out, "{line}")?;
    out.flush()
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let config = match config(&args) {
        Ok(config) => config,
        Err(e) => {
            eprintln!("pulse-stand-in: {e}");
            return ExitCode::from(2);
        }
    };
    let socket = config.socket.clone();
    let stand_in = match StandIn::start(config) {
        Ok(stand_in) => stand_in,
        Err(e) => {
            eprintln!("pulse-stand-in: {e}");
            return ExitCode::FAILURE;
        }
    };
    let mut out = io::stdout().lock();
    let mut served = say(&mut out, &json!({"ready": true}));
    for line in io::stdin().lock().lines() {
        if served.is_err() {
            break;
        }
        let answer = match line {
            Err(e) => {
                served = Err(e);
                break;
            }
            Ok(line) => match serde_json::from_str::<Instruction>(&line) {
                Err(e) => json!({"ok": false, "detail": format!("unreadable instruction: {e}")}),
                Ok(instruction) => match stand_in.instruct(instruction) {
                    Ok(Value::Object(mut fields)) => {
                        fields.insert("ok".to_string(), Value::Bool(true));
                        Value::Object(fields)
                    }
                    Ok(other) => json!({"ok": true, "value": other}),
                    Err(detail) => json!({"ok": false, "detail": detail}),
                },
            },
        };
        served = say(&mut out, &answer);
    }
    let _ = std::fs::remove_file(&socket);
    match served {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("pulse-stand-in: {e}");
            ExitCode::FAILURE
        }
    }
}
