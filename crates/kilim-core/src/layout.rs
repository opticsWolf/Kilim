//! Layout model — the contract between ratatui and Lace.
//!
//! Mirrors `Lace/DockManager.save_state()` shape (nested splits + tab groups)
//! but as serde JSON so both frontends load the same `layouts/*.json`
//! (Lace perspective == ratatui layout).

use serde::{Deserialize, Serialize};

/// Root layout document.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Layout {
    pub root: Node,
    /// Focused pane id.
    #[serde(default)]
    pub active: String,
    /// Theme for code panes (TUI file panes + Qt FilePane).
    #[serde(default = "default_theme")]
    pub theme: String,
    /// Theme for fenced code in markdown previews (Qt MarkdownPane +
    /// TUI markdown-as-source panes). Split from `theme` in v0.1.24.
    #[serde(default = "default_markdown_theme")]
    pub markdown_theme: String,
}

/// Default code theme: unified Kilim Midnight where available (markdown
/// feature carries bat assets + Kilim themes); plain syntect default
/// otherwise so the default is always in-registry (no silent fallback).
#[cfg(feature = "markdown")]
fn default_theme() -> String {
    "Kilim Midnight".to_string()
}

#[cfg(not(feature = "markdown"))]
fn default_theme() -> String {
    "InspiredGitHub".to_string()
}

#[cfg(feature = "markdown")]
fn default_markdown_theme() -> String {
    "Kilim Midnight".to_string()
}

#[cfg(not(feature = "markdown"))]
fn default_markdown_theme() -> String {
    "InspiredGitHub".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Node {
    Split {
        dir: Dir,
        /// 0.0..1.0 fraction for the first child.
        ratio: f32,
        a: Box<Node>,
        b: Box<Node>,
    },
    Tabs {
        tabs: Vec<Tab>,
        current: usize,
    },
    Pane {
        pane_id: String,
    },
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Dir {
    Horizontal,
    Vertical,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Tab {
    pub pane_id: String,
    pub title: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Pane {
    pub id: String,
    pub title: String,
    #[serde(flatten)]
    pub kind: PaneKind,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum PaneKind {
    /// Terminal pane backed by stitch-pty (`spawn_platform`). `cmd` may be
    /// omitted in the layout: an empty cmd spawns the platform default
    /// ([`crate::shell::default_shell`]), keeping layouts portable.
    Term {
        #[serde(default)]
        cmd: String,
        #[serde(default)]
        args: Vec<String>,
        #[serde(default = "default_rows")]
        rows: u16,
        #[serde(default = "default_cols")]
        cols: u16,
        #[serde(default = "default_scrollback")]
        scrollback: usize,
    },
    /// File viewer pane, highlighted via syntect.
    File {
        path: String,
    },
    /// Markdown preview pane (requires `markdown` feature / mordant).
    Markdown {
        path: String,
    },
}

fn default_rows() -> u16 {
    24
}
fn default_cols() -> u16 {
    80
}
fn default_scrollback() -> usize {
    5000
}

impl Layout {
    pub fn from_json(s: &str) -> Result<(Self, Vec<Pane>), serde_json::Error> {
        // File format: { "layout": Layout, "panes": [Pane] }
        #[derive(Deserialize)]
        struct Doc {
            layout: Layout,
            #[serde(default)]
            panes: Vec<Pane>,
        }
        let doc: Doc = serde_json::from_str(s)?;
        Ok((doc.layout, doc.panes))
    }

    /// All pane ids referenced by the tree (for validation).
    pub fn pane_ids(&self) -> Vec<String> {
        let mut out = Vec::new();
        self.root.collect_panes(&mut out);
        out
    }

    /// Cycle the visible tab of the Tabs group containing `pane_id`.
    /// Active follows the newly shown tab. Returns false if not tabbed.
    pub fn cycle_tab(&mut self, pane_id: &str, dir: i32) -> bool {
        if let Some((tabs, current)) = self.root.find_tabs_mut(pane_id) {
            if tabs.len() > 1 {
                let cur = tabs.iter().position(|t| t.pane_id == pane_id).unwrap_or(*current);
                let next = (cur as i32 + dir).rem_euclid(tabs.len() as i32) as usize;
                *current = next;
                self.active = tabs[next].pane_id.clone();
                return true;
            }
        }
        false
    }
}

impl Node {
    /// Mutable tab list + current index of the Tabs group containing `pane_id`.
    fn find_tabs_mut(&mut self, pane_id: &str) -> Option<(&mut Vec<Tab>, &mut usize)> {
        match self {
            Node::Tabs { tabs, current } if tabs.iter().any(|t| t.pane_id == pane_id) => {
                Some((tabs, current))
            }
            Node::Split { a, b, .. } => a.find_tabs_mut(pane_id).or_else(|| b.find_tabs_mut(pane_id)),
            _ => None,
        }
    }

    fn collect_panes(&self, out: &mut Vec<String>) {
        match self {
            Node::Pane { pane_id } => out.push(pane_id.clone()),
            Node::Split { a, b, .. } => {
                a.collect_panes(out);
                b.collect_panes(out);
            }
            Node::Tabs { tabs, .. } => {
                for t in tabs {
                    out.push(t.pane_id.clone());
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pane_ids_walk() {
        let l = Layout {
            root: Node::Split {
                dir: Dir::Horizontal,
                ratio: 0.5,
                a: Box::new(Node::Pane {
                    pane_id: "a".into(),
                }),
                b: Box::new(Node::Tabs {
                    tabs: vec![
                        Tab {
                            pane_id: "b".into(),
                            title: "b".into(),
                        },
                        Tab {
                            pane_id: "c".into(),
                            title: "c".into(),
                        },
                    ],
                    current: 0,
                }),
            },
            active: "a".into(),
            theme: default_theme(),
            markdown_theme: default_markdown_theme(),
        };
        assert_eq!(l.pane_ids(), vec!["a", "b", "c"]);
    }

    #[test]
    fn term_scrollback_defaults_and_parses() {
        let doc = r#"{"layout": {"root": {"type": "pane", "pane_id": "t"}, "active": "t", "theme": "InspiredGitHub"}, "panes": [{"id": "t", "title": "t", "kind": "term", "cmd": "sh"}]}"#;
        let (_, panes) = Layout::from_json(doc).unwrap();
        assert!(matches!(
            &panes[0].kind,
            PaneKind::Term { scrollback: 5000, rows: 24, cols: 80, .. }
        ));
        let doc2 = doc.replace("\"cmd\": \"sh\"", "\"cmd\": \"sh\", \"scrollback\": 42");
        let (_, panes2) = Layout::from_json(&doc2).unwrap();
        assert!(matches!(&panes2[0].kind, PaneKind::Term { scrollback: 42, .. }));
    }

    #[test]
    fn term_cmd_defaults_to_platform_shell() {
        // Portable layouts omit cmd; the pane parses with an empty one,
        // which TermHandle::spawn resolves to the platform default.
        let doc = r#"{"layout": {"root": {"type": "pane", "pane_id": "t"}, "active": "t"}, "panes": [{"id": "t", "title": "t", "kind": "term"}]}"#;
        let (_, panes) = Layout::from_json(doc).unwrap();
        match &panes[0].kind {
            PaneKind::Term { cmd, rows, cols, .. } => {
                assert!(cmd.is_empty());
                assert_eq!((*rows, *cols), (24, 80));
            }
            other => panic!("expected a term pane, got {other:?}"),
        }
    }

    #[test]
    fn theme_fields_default_together() {
        // Old docs without the fields still load; both default alike.
        let doc = r#"{"layout": {"root": {"type": "pane", "pane_id": "t"}, "active": "t"}, "panes": []}"#;
        let (l, _) = Layout::from_json(doc).unwrap();
        assert_eq!(l.theme, l.markdown_theme);
        #[cfg(feature = "markdown")]
        assert_eq!(l.theme, "Kilim Midnight");
        #[cfg(not(feature = "markdown"))]
        assert_eq!(l.theme, "InspiredGitHub");
    }

    #[test]
    fn cycle_tab_moves_active_and_current() {
        let doc = r#"{"layout": {"root": {"type": "tabs", "tabs": [{"pane_id": "a", "title": "a"}, {"pane_id": "b", "title": "b"}], "current": 0}, "active": "a", "theme": "x"}, "panes": []}"#;
        let (mut l, _) = Layout::from_json(doc).unwrap();
        assert!(l.cycle_tab("a", 1));
        assert_eq!(l.active, "b");
        assert!(l.cycle_tab("b", 1));
        assert_eq!(l.active, "a"); // wraps
        assert!(!l.cycle_tab("ghost", 1));
    }
}
