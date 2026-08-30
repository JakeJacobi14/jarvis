#!/usr/bin/env bash
# Double-click this in Finder (or run it) to open the full Jarvis stack.
# Same as ./start.sh — including auto-download of spotifyd so music can
# play without the Spotify app sitting open.
cd "$(dirname "$0")"
exec ./start.sh
