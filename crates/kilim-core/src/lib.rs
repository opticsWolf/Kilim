//! kilim-core: headless state for Kilim (setup B).
//!
//! No ratatui, no crossterm, no Qt, no PyO3 here.
//! Both frontends render the same [`layout::Layout`]:
//! - `kilim-tui` draws it with ratatui splits/tabs
//! - `kilim-py` exposes it to Lace as dock areas + perspectives

pub mod highlight;
pub mod kilim_themes;
pub mod layout;
pub mod session;
pub mod term;

pub use highlight::{StyledSpan, ThemeRegistry};
pub use layout::{Dir, Layout, Node, Pane, PaneKind, Tab};
pub use session::Session;
pub use term::TermHandle;
