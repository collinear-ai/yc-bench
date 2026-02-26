"""Generate a GIF showing scratchpad/notepad evolution over turns."""
import json
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent.parent


def extract_scratchpad_versions(result_path):
    """Extract all scratchpad write commands from a result JSON transcript."""
    with open(result_path) as f:
        d = json.load(f)

    versions = []
    for t in d["transcript"]:
        for cmd in t.get("commands_executed", []):
            if "scratchpad write" not in cmd.lower():
                continue
            idx = cmd.find("--content ")
            if idx < 0:
                continue
            content_start = idx + len("--content ")
            if cmd[content_start] == '"':
                content_start += 1
            arrow = cmd.find(' -> {')
            if arrow > 0:
                content = cmd[content_start:arrow].rstrip('"')
            else:
                content = cmd[content_start:].rstrip('"')
            # Unescape
            content = content.replace("\\n", "\n").replace('\\"', '"')
            versions.append({
                "turn": t["turn"],
                "content": content,
            })
    return versions, d


def render_frame(content, turn, total_turns, meta, frame_size=(1200, 800)):
    """Render a single scratchpad frame as a PIL Image."""
    w, h = frame_size
    img = Image.new("RGB", (w, h), "#ffffff")
    draw = ImageDraw.Draw(img)

    # Try to use a monospace font
    try:
        body_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
        small_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 11)
    except (OSError, IOError):
        body_font = ImageFont.load_default()
        title_font = body_font
        small_font = body_font

    # Header bar
    draw.rectangle([(0, 0), (w, 50)], fill="#1a1a2e")
    model_label = meta.get("model", "unknown")
    config = meta.get("config", "")
    seed = meta.get("seed", "")
    outcome = meta.get("outcome", "")
    outcome_color = "#4ade80" if "survived" in outcome.lower() else "#f87171"

    draw.text((16, 8), f"SCRATCHPAD", fill="#e2e8f0", font=title_font)
    draw.text((180, 8), f"{model_label}", fill="#94a3b8", font=small_font)
    draw.text((180, 26), f"{config} · seed {seed}", fill="#64748b", font=small_font)

    # Turn indicator + progress bar
    draw.text((w - 280, 8), f"Turn {turn}/{total_turns}", fill="#e2e8f0", font=title_font)
    draw.text((w - 280, 30), outcome, fill=outcome_color, font=small_font)

    bar_x, bar_y, bar_w, bar_h = w - 130, 15, 110, 20
    draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], outline="#334155", width=1)
    progress = min(turn / max(total_turns, 1), 1.0)
    fill_color = "#3b82f6" if "survived" not in outcome.lower() else "#22c55e"
    draw.rectangle([(bar_x + 1, bar_y + 1), (bar_x + 1 + int((bar_w - 2) * progress), bar_y + bar_h - 1)], fill=fill_color)

    # Content area
    margin = 20
    y = 60
    max_width = 115  # characters per line

    lines = []
    for raw_line in content.split("\n"):
        if len(raw_line) <= max_width:
            lines.append(raw_line)
        else:
            wrapped = textwrap.wrap(raw_line, width=max_width, break_long_words=True, break_on_hyphens=False)
            lines.extend(wrapped if wrapped else [""])

    max_lines = (h - y - 20) // 16

    for i, line in enumerate(lines[:max_lines]):
        text_y = y + i * 16

        # Color coding
        if line.startswith("##") or line.startswith("==="):
            color = "#1e40af"
            draw.text((margin, text_y), line, fill=color, font=body_font)
        elif "CRISIS" in line or "LOCKED" in line or "LATE" in line or "FAIL" in line or "bankrupt" in line.lower():
            color = "#dc2626"
            draw.text((margin, text_y), line, fill=color, font=body_font)
        elif "LESSON" in line or "KEY" in line or "RULE" in line or "STRATEGY" in line:
            color = "#7c3aed"
            draw.text((margin, text_y), line, fill=color, font=body_font)
        elif line.startswith("- ") or line.startswith("  -"):
            draw.text((margin, text_y), line, fill="#374151", font=body_font)
        elif "✅" in line or "SUCCESS" in line or "survived" in line.lower():
            draw.text((margin, text_y), line, fill="#16a34a", font=body_font)
        else:
            draw.text((margin, text_y), line, fill="#1f2937", font=body_font)

    if len(lines) > max_lines:
        draw.text((margin, y + max_lines * 16), f"  ... ({len(lines) - max_lines} more lines)", fill="#9ca3af", font=small_font)

    # Bottom border
    draw.line([(0, h - 2), (w, h - 2)], fill="#e5e7eb", width=1)

    return img


def make_gif(result_path, output_path=None):
    versions, data = extract_scratchpad_versions(result_path)
    if not versions:
        print(f"No scratchpad writes found in {result_path}")
        return

    total_turns = data.get("turns_completed", versions[-1]["turn"])
    model = data.get("model", "unknown").split("/")[-1]
    reason = data.get("terminal_reason", "unknown")
    outcome = "SURVIVED" if reason == "horizon_end" else reason.upper()

    # Infer config from filename
    fname = Path(result_path).stem
    config_match = re.search(r"result_(\w+)_\d+_", fname)
    config = config_match.group(1) if config_match else "unknown"
    seed_match = re.search(r"_(\d+)_anthropic", fname) or re.search(r"_(\d+)_gemini", fname)
    seed = seed_match.group(1) if seed_match else "?"

    meta = {"model": model, "config": config, "seed": seed, "outcome": outcome}

    print(f"Generating GIF: {len(versions)} frames, {model}, {config} seed={seed}, {outcome}")

    frames = []
    for v in versions:
        frame = render_frame(v["content"], v["turn"], total_turns, meta)
        frames.append(frame)

    if not output_path:
        output_path = ROOT / "plots" / f"notepad_{config}_{seed}_{model}.gif"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Each frame shown for 3 seconds, last frame for 6 seconds
    durations = [3000] * len(frames)
    if durations:
        durations[-1] = 6000

    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
    )
    print(f"Saved: {output_path} ({len(frames)} frames)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        make_gif(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        # Generate for all available result files
        for p in sorted(ROOT.glob("results/yc_bench_result_*.json")):
            make_gif(p)
