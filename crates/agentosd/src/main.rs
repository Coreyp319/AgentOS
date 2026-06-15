//! agentosd — AgentOS resource broker.
//!
//! v0 implements ONLY the read-only VRAM monitor. It proves the load-bearing,
//! previously-unverified pieces of the VRAM coordinator WITHOUT doing anything
//! destructive:
//!   * read GPU VRAM via NVML (the `nvml-wrapper` crate — unverified by the
//!     research pass, so this is the verification)
//!   * read Ollama's loaded models + their VRAM via `GET /api/ps`
//!   * estimate the graphics (wallpaper + compositor) footprint and a pressure verdict
//!
//! No eviction, no `ollama stop`, no nimbus-flux kill/relaunch yet — see
//! docs/adr/0004. Every decision here is logged dry.

use std::{thread, time::Duration};

use nvml_wrapper::Nvml;
use serde::Deserialize;

const OLLAMA_PS: &str = "http://127.0.0.1:11434/api/ps";

/// On a 24GB card, the largest local model is ~21GB. Warn when free VRAM drops
/// below the headroom we'd need to load it alongside graphics. (Tuned later.)
const PRESSURE_FREE_MIB: u64 = 3000;

#[derive(Deserialize)]
struct PsResp {
    #[serde(default)]
    models: Vec<PsModel>,
}

#[derive(Deserialize)]
struct PsModel {
    name: String,
    #[serde(default)]
    size_vram: u64, // bytes
}

fn mib(bytes: u64) -> u64 {
    bytes / (1024 * 1024)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mode = std::env::args().nth(1).unwrap_or_else(|| "monitor".into());
    if mode != "monitor" {
        eprintln!("agentosd v0: only `monitor` (read-only) is implemented. See docs/adr/.");
        std::process::exit(2);
    }

    let nvml = match Nvml::init() {
        Ok(n) => n,
        Err(e) => {
            eprintln!("NVML init failed (is the NVIDIA driver loaded?): {e}");
            std::process::exit(1);
        }
    };
    let device = nvml.device_by_index(0)?;
    let gpu_name = device.name().unwrap_or_else(|_| "GPU0".into());

    println!("agentosd monitor (read-only) — {gpu_name}");
    println!(
        "{:<8} {:>9} {:>9} {:>9}  {:<8} {}",
        "time", "total", "used", "free", "verdict", "gfx / ollama models"
    );

    let http = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()?;

    loop {
        let mem = device.memory_info()?;
        let (total, used, free) = (mib(mem.total), mib(mem.used), mib(mem.free));

        // Ollama loaded models (fail-open: if unreachable, treat as none loaded).
        let (model_desc, model_vram_mib): (String, u64) =
            match http.get(OLLAMA_PS).send().and_then(|r| r.json::<PsResp>()) {
                Ok(ps) if !ps.models.is_empty() => {
                    let total_v: u64 = ps.models.iter().map(|m| mib(m.size_vram)).sum();
                    let names: Vec<String> = ps
                        .models
                        .iter()
                        .map(|m| format!("{}({}MiB)", m.name, mib(m.size_vram)))
                        .collect();
                    (names.join(", "), total_v)
                }
                Ok(_) => ("(no model loaded)".into(), 0),
                Err(_) => ("(ollama unreachable)".into(), 0),
            };

        // Everything in `used` that isn't an Ollama model ≈ graphics + compositor + other.
        let graphics_mib = used.saturating_sub(model_vram_mib);
        let verdict = if free < PRESSURE_FREE_MIB { "PRESSURE" } else { "ok" };

        println!(
            "{:<8} {:>8}M {:>8}M {:>8}M  {:<8} gfx≈{}MiB | {}",
            now_hms(),
            total,
            used,
            free,
            verdict,
            graphics_mib,
            model_desc
        );

        thread::sleep(Duration::from_secs(2));
    }
}

/// HH:MM:SS (UTC) without pulling a date crate — adequate for a monitor log.
fn now_hms() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let s = secs % 86_400;
    format!("{:02}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60)
}
