"""Streamlit version-compat helpers.

Newer Streamlit prefers ``width="stretch"``; older releases only know
``use_container_width``.  These tiny wrappers keep the app working on any
supported version without deprecation noise.
"""
from __future__ import annotations

import streamlit as st

_CONFIG = {"displayModeBar": False}


def plotly(fig) -> None:
    try:
        st.plotly_chart(fig, width="stretch", config=_CONFIG)
    except TypeError:  # pragma: no cover - older streamlit
        st.plotly_chart(fig, use_container_width=True, config=_CONFIG)


def dataframe(data, height: int | None = None, **kwargs) -> None:
    kwargs.setdefault("hide_index", True)
    try:
        st.dataframe(data, width="stretch", height=height, **kwargs)
    except TypeError:  # pragma: no cover - older streamlit
        st.dataframe(data, use_container_width=True, height=height, **kwargs)


def button(label: str, **kwargs):
    try:
        return st.button(label, width="stretch", **kwargs)
    except TypeError:  # pragma: no cover - older streamlit
        return st.button(label, use_container_width=True, **kwargs)


def html(html: str, height: int) -> None:
    """Render inline HTML (st.iframe on new streamlit, components.v1.html on old)."""
    try:
        st.iframe(html, height=height)
    except TypeError:  # pragma: no cover - older streamlit
        from streamlit.components import v1 as html_components

        html_components.html(html, height=height)
