//! kilim TUI: ratatui frontend over kilim-core + stitch-pty.
//!
//! Usage: `kilim layouts/default.json`

mod app;
mod ui;

use clap::Parser;

#[derive(Parser)]
struct Args {
    /// Layout doc: { "layout": Layout, "panes": [Pane] }
    layout: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    let doc = std::fs::read_to_string(&args.layout)?;
    let mut app = app::App::new(&doc)?;
    app.layout_path = Some(args.layout.clone());
    app.run().await?;
    Ok(())
}
