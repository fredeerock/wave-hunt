#!/bin/zsh
cd "$(dirname "$0")"
exec uv run app.py
