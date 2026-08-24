"""
render_cards — 正文配图卡片渲染（HTML → PNG）

**纯器械**：把你手写的 `post_cards/p1.html … pN.html` 用无头 chromium 按 3:4 竖版
截成 `post_images/p1.png … pN.png`，供发布为多图帖。它不挑图、不排版、不做任何设计
决策——卡片长什么样完全由你写的 HTML 决定（设计规范见 references/card-design.md）。

同时做**渲染层自检**（静态读 HTML 看不出来、只有渲染后才暴露的缺陷）：
  1. 图裂（naturalWidth==0）——多半是 `../figures/<name>` 路径写错
  2. 图变形（渲染宽高比与原始宽高比差 >0.02）——卡片里最常见的是同时钉死 width+height
  3. 竖向溢出（scrollHeight 超出 1440）——内容被截掉，手机上看就是话说一半
  4. 横向溢出（scrollWidth 超出 1080）——右边被切
四项都是硬指标，任一不过 → 该卡 FAIL、脚本退出码 1（PNG 仍会写出，方便你 Read 看着改）。

输入：post_cards/p<N>.html（你写）
输出：post_images/p<N>.png（清空旧 p*）、logs/render_cards_result.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

import _env  # noqa: F401  # 独立运行时兜底加载包根 .env

from utils import (
    load_json,
    print_error,
    print_info,
    print_stage_header,
    print_success,
    print_warning,
    resolve_workspace,
    save_stage_result,
)

CARD_W = 1080   # 小红书竖版 3:4
CARD_H = 1440
SCALE = 2       # 2x 输出（2160×2880），保证小字在手机上不糊

# 渲染层自检：与 paper2html/scripts/render_check.py 同源，裁到卡片场景需要的四项
_JS = r"""
() => {
  const px = (v) => parseFloat(v) || 0;
  const imgs = [...document.querySelectorAll('img')].map((im) => {
    const r = im.getBoundingClientRect();
    const st = getComputedStyle(im);
    const cw = r.width  - px(st.borderLeftWidth) - px(st.borderRightWidth) - px(st.paddingLeft) - px(st.paddingRight);
    const ch = r.height - px(st.borderTopWidth)  - px(st.borderBottomWidth) - px(st.paddingTop) - px(st.paddingBottom);
    const broken = !im.naturalWidth || !im.naturalHeight;
    const ok = !broken && ch > 0;
    return {
      alt: (im.alt || '').slice(0, 40),
      src: (im.getAttribute('src') || '').slice(-60),
      natW: im.naturalWidth, natH: im.naturalHeight,
      renW: Math.round(cw), renH: Math.round(ch),
      broken,
      ratioErr: ok ? +Math.abs((cw / ch) - (im.naturalWidth / im.naturalHeight)).toFixed(3) : null,
      upscale: ok && im.naturalWidth ? +(cw / im.naturalWidth).toFixed(2) : null,
    };
  });
  const de = document.documentElement;
  // 竖版卡片很自然会写 html,body{overflow:hidden}——那样超出的内容被裁掉、scrollHeight 恒等于
  // clientHeight，溢出就永远测不出来（截图里内容已经缺了一块，自检却报 PASS）。**截图已经拍完**，
  // 此刻改样式不影响产物：先把 overflow 放开再量，让被裁的部分重新撑出来。
  const hidden = getComputedStyle(de).overflow !== 'visible'
              || (document.body && getComputedStyle(document.body).overflow !== 'visible');
  de.style.overflow = 'visible';
  if (document.body) document.body.style.overflow = 'visible';
  void de.offsetHeight;  // 强制 reflow，确保下面量到的是放开后的尺寸
  return {imgs, scrollW: de.scrollWidth, scrollH: de.scrollHeight,
          innerW: window.innerWidth, innerH: window.innerHeight,
          overflowWasHidden: hidden};
}
"""


def _card_index(p: Path) -> int:
    """p12.html → 12；按序号排而非字典序，否则 p10 会排到 p2 前面。"""
    return int(re.sub(r"\D", "", p.stem) or "0")


def _expected_card_count(workspace: dict) -> int | None:
    """xhs_post.json 的 qa 条数——一问一图，卡片数应与问题数一致。读不到就不校验。"""
    post_path = workspace["xhs"] / "xhs_post.json"
    if not post_path.exists():
        return None
    try:
        qa = load_json(post_path).get("qa")
    except (json.JSONDecodeError, OSError):
        return None
    return len(qa) if isinstance(qa, list) and qa else None


def _check(data: dict) -> dict:
    """渲染数据 → 四项硬指标 + 一项软告警。"""
    imgs = data["imgs"]
    v_overflow = data["scrollH"] - data["innerH"]
    h_overflow = data["scrollW"] - data["innerW"]
    return {
        "broken_images": [im for im in imgs if im["broken"]],
        "distorted_images": [im for im in imgs
                             if im["ratioErr"] is not None and im["ratioErr"] > 0.02],
        "v_overflow_px": v_overflow,
        "h_overflow_px": h_overflow,
        "overflow_was_hidden": data.get("overflowWasHidden", False),
        "upscaled_images_warn": [im for im in imgs
                                 if im["upscale"] is not None and im["upscale"] > 1.5],
        "n_images": len(imgs),
    }


def run(workdir: str, width: int = CARD_W, height: int = CARD_H, scale: int = SCALE) -> dict:
    """渲染 post_cards/*.html → post_images/p<N>.png，并做渲染层自检。"""
    print_stage_header("渲染正文配图卡片")

    workspace = resolve_workspace(workdir)
    cards_dir = workspace["root"] / "post_cards"
    cards = sorted((p for p in cards_dir.glob("p[0-9]*.html") if p.is_file()),
                   key=_card_index)
    if not cards:
        print_error(f"没有找到卡片 HTML：{cards_dir}/p1.html …")
        print_info("一问一图：正文每个问题对应一张卡片，先按 references/card-design.md 手写它们")
        result = {"status": "failed", "error": f"{cards_dir} 下无 p<N>.html"}
        save_stage_result(result, "render_cards", workspace)
        return result

    expected = _expected_card_count(workspace)
    if expected is not None and expected != len(cards):
        print_warning(f"卡片 {len(cards)} 张 ≠ 正文问题 {expected} 个——一问一图，请对齐后重跑")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print_error("未安装 playwright：conda run -n paper2anything python -m playwright install chromium")
        result = {"status": "failed", "error": "playwright 未安装"}
        save_stage_result(result, "render_cards", workspace)
        return result

    out_dir = workspace["root"] / "post_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("p[0-9]*"):  # 重跑覆盖：先清旧图，避免残留多余的 pN
        if stale.is_file():
            stale.unlink()

    reports, images = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height},
                                device_scale_factor=scale)
        for card in cards:
            dst = out_dir / f"p{_card_index(card)}.png"
            page.goto(card.resolve().as_uri(), wait_until="networkidle")
            page.wait_for_timeout(400)  # 等相对路径图片 / 字体落地，避免截到半渲染帧
            page.screenshot(path=str(dst), full_page=False)
            rep = _check(page.evaluate(_JS))
            rep["card"] = card.name
            rep["png"] = str(dst)
            rep["ok"] = (not rep["broken_images"] and not rep["distorted_images"]
                         and rep["v_overflow_px"] <= 2 and rep["h_overflow_px"] <= 2)
            reports.append(rep)
            images.append(str(dst))
            if rep["ok"]:
                print_success(f"{dst.name} ← {card.name}")
            else:
                issues = []
                if rep["broken_images"]:
                    issues.append(f"图裂 {len(rep['broken_images'])} 张（查 ../figures/ 路径）")
                if rep["distorted_images"]:
                    issues.append(f"图变形 {len(rep['distorted_images'])} 张（别同时钉死宽高）")
                if rep["v_overflow_px"] > 2:
                    clipped = "，overflow:hidden 已把它裁掉、截图里就是缺的" if rep["overflow_was_hidden"] else ""
                    issues.append(f"竖向溢出 {rep['v_overflow_px']}px（内容装不下{clipped}，删字或缩图）")
                if rep["h_overflow_px"] > 2:
                    issues.append(f"横向溢出 {rep['h_overflow_px']}px")
                print_error(f"{dst.name} ← {card.name}：" + "；".join(issues))
        browser.close()

    failed = [r["card"] for r in reports if not r["ok"]]
    result = {
        "status": "success" if not failed else "failed",
        "count": len(images),
        "images": images,
        "expected_cards": expected,
        "failed_cards": failed,
        "size": f"{width}x{height}@{scale}x",
        "reports": reports,
    }
    save_stage_result(result, "render_cards", workspace)

    if failed:
        print_error(f"{len(failed)}/{len(cards)} 张卡片未通过渲染自检：{', '.join(failed)}")
        print_info(f"PNG 已写出（{out_dir}），Read 看一眼、改 HTML 后重跑本命令")
    else:
        print_success(f"卡片就绪：{len(images)} 张（{out_dir}）")
        print_info("逐张 Read 亲眼过一遍：字号够大吗？留白匀吗？和封面是一套吗？")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="正文配图卡片渲染 HTML→PNG（协调式机械步骤）")
    parser.add_argument(
        "--workdir", required=True,
        help="工作区目录，约定 <pdf目录>/.paper2anything/xhs/<stem>",
    )
    parser.add_argument("--width", type=int, default=CARD_W, help=f"卡片宽 px（默认 {CARD_W}）")
    parser.add_argument("--height", type=int, default=CARD_H, help=f"卡片高 px（默认 {CARD_H}，3:4 竖版）")
    parser.add_argument("--scale", type=int, default=SCALE, help=f"输出倍率（默认 {SCALE}x）")
    args = parser.parse_args()
    res = run(args.workdir, args.width, args.height, args.scale)
    sys.exit(0 if res.get("status") == "success" else 1)
