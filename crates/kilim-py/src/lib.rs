//! kilim-py: thin PyO3 surface. GIL released during work (stitch-pty pattern).

#[cfg(feature = "python")]
mod python_api;

#[cfg(feature = "python")]
use pyo3::prelude::*;

#[cfg(feature = "python")]
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<python_api::CoreSession>()?;
    m.add_function(wrap_pyfunction!(python_api::list_themes, m)?)?;
    m.add_function(wrap_pyfunction!(python_api::list_syntaxes, m)?)?;
    m.add_function(wrap_pyfunction!(python_api::kilim_themes_json, m)?)?;
    m.add_function(wrap_pyfunction!(python_api::theme_background, m)?)?;
    m.add_function(wrap_pyfunction!(python_api::theme_foreground, m)?)?;
    #[cfg(feature = "markdown")]
    m.add_function(wrap_pyfunction!(python_api::markdown_theme_names, m)?)?;
    Ok(())
}
