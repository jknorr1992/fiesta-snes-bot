#!/usr/bin/env python3
"""Validate the field renderer against the emulator: load a save state in the running test bed, take a SCREENSHOT, read the same state
back over the network (live mode) and compare the renderer's output with the screenshot pixel by pixel.
Usage: screen_check.py <rom> <slot|state file> ...   (env FFV_STATES / FFV_SHOTS as in play.py; nothing is hardcoded)"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image

def native(shot_path):
    """RetroArch screenshot (aspect-corrected, 8:7 pixels) -> 256x224 RGB by sampling pixel centres"""
    im = np.array(Image.open(shot_path).convert("RGB"))
    h, w = im.shape[:2]
    xs = ((np.arange(256) + 0.5) * w / 256).astype(int); ys = ((np.arange(224) + 0.5) * h / 224).astype(int)
    return im[ys][:, xs]

def compare(a, b, tol=0):
    d = np.abs(a.astype(int) - b.astype(int)).max(axis=2)
    return (d <= tol).mean(), d

def diff_image(a, b, d, path):
    c = np.concatenate([a, b, np.stack([np.minimum(255, d * 8)] * 3, axis=2).astype(np.uint8)], axis=1)
    Image.fromarray(c).resize((c.shape[1] * 2, c.shape[0] * 2), Image.NEAREST).save(path)

def check_slot(game, rom, slot, state_path, out_prefix=None):
    """load `slot` in the running emulator, screenshot it and compare with the render built from `state_path` (same state)"""
    from snes9x_state import State
    from field_screen import Field, Wram, StateWram
    game.load(slot)
    time.sleep(0.3)
    shot = game.shot()
    nat = native(shot)
    img = Field(rom).render(Wram(StateWram(State(state_path))))
    f, d = compare(img, nat)
    if out_prefix: diff_image(img, nat, d, out_prefix + ".png")
    return f, d
