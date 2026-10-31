"""9:16 / 4:5 / 원본 비율 출력 지오메트리. Fit(원본 전체 유지 + #000000 여백, 확대하면 넘치는 부분은 크롭) / Fill(크롭) 계산.
무의미한 업스케일링 방지. 원본 비율(orig)은 여백도 크롭도 없이 원본과 같은 비율로 출력한다 (확대하면 크롭)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction

ASPECT_916 = Fraction(9, 16)
ASPECT_45 = Fraction(4, 5)

# (폭, 높이) 후보: 첫 번째가 기본(1080 기준), 마지막이 호환 최소 해상도
TIERS = {
    "916": [(1080, 1920), (900, 1600), (720, 1280)],
    "45": [(1080, 1350), (864, 1080), (720, 900)],
}


@dataclass
class LayoutParams:
    mode: str = "fit"  # 'fit' | 'fill'
    zoom: float = 1.0  # >=1. fit/fill 모두 확대 가능 (v2 부터 fit 도 확대)
    pos_x: float = 0.5  # 0..1  (여백이 있으면 여백 안에서의 위치, 크롭하면 크롭 창 위치)
    pos_y: float = 0.5
    zv: int = 2  # 데이터 버전. 1 이하(옛 저장본)의 fit 은 확대가 비활성이었으므로 남아 있는 zoom 값을 무시한다

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        mode = d.get("mode", "fit")
        zoom = float(d.get("zoom", 1.0))
        if mode == "fit" and int(d.get("zv", 1)) < 2:
            zoom = 1.0  # 예전에는 fit 에서 확대가 꺼져 있었다: fill 때 남은 값으로 결과가 바뀌면 안 된다
        return cls(mode=mode, zoom=zoom, pos_x=float(d.get("pos_x", 0.5)), pos_y=float(d.get("pos_y", 0.5)), zv=2)


@dataclass
class Geometry:
    mode: str
    canvas_w: int
    canvas_h: int
    # fit
    content_w: int = 0
    content_h: int = 0
    pad_x: int = 0
    pad_y: int = 0
    # 크롭 창 (소스 표시 좌표계 기준). fill 은 항상, fit 은 확대로 넘칠 때 (전체면 crop_w/h == 표시 크기)
    crop_x: int = 0
    crop_y: int = 0
    crop_w: int = 0
    crop_h: int = 0
    scale: float = 1.0  # 소스 -> 출력 배율 (1 초과 = 업스케일)
    upscaled: bool = False
    tier_note: str = ""

    def to_dict(self):
        return asdict(self)


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


def _even_floor(v: float) -> int:
    return max(2, int(v // 2) * 2)


def compute_geometry(disp_w: int, disp_h: int, kind: str, params: LayoutParams | None = None) -> Geometry:
    """kind: '916' | '45' | 'orig'(원본 비율).  disp_*: SAR/회전 반영한 정사각 픽셀 기준 표시 크기."""
    params = params or LayoutParams()
    orig = kind == "orig"
    if orig:
        w0 = _even(min(1080, disp_w))
        tiers = [(w0, _even(w0 * disp_h / disp_w))]
        aspect = Fraction(tiers[0][0], tiers[0][1])
        mode = "fill"  # 여백이 없으므로 확대는 크롭으로 표현한다
    else:
        aspect = ASPECT_916 if kind == "916" else ASPECT_45
        tiers = TIERS[kind]
        mode = params.mode
    zoom = max(1.0, float(params.zoom))
    px = min(1.0, max(0.0, params.pos_x))
    py = min(1.0, max(0.0, params.pos_y))

    def scale_for(cw_: float, ch_: float, W: int, H: int) -> float:
        return min(W / cw_, H / ch_)

    if mode == "fill":
        if orig:
            cw0, ch0 = float(disp_w), float(disp_h)
        else:
            cw0 = min(float(disp_w), disp_h * float(aspect))
            ch0 = cw0 / float(aspect)
        cw, ch = cw0 / zoom, ch0 / zoom
        # 선택: 업스케일이 없는 가장 큰 티어, 없으면 최소 티어
        chosen = tiers[-1]
        for W, H in tiers:
            if W / cw <= 1.0 + 1e-9:
                chosen = (W, H)
                break
        W, H = chosen
        s = W / cw
        icw, ich = int(round(cw)), int(round(ch))
        icw, ich = min(icw, disp_w), min(ich, disp_h)
        cx = int(round(px * (disp_w - icw)))
        cy = int(round(py * (disp_h - ich)))
        cx -= cx % 2
        cy -= cy % 2
        g = Geometry("fill", W, H, crop_x=cx, crop_y=cy, crop_w=icw, crop_h=ich, scale=s, upscaled=s > 1.0 + 1e-9)
    else:
        chosen = tiers[-1]
        for W, H in tiers:
            if scale_for(disp_w, disp_h, W, H) * zoom <= 1.0 + 1e-9:
                chosen = (W, H)
                break
        W, H = chosen
        s = scale_for(disp_w, disp_h, W, H) * zoom  # 소스 -> 출력 배율 (확대 포함)
        # 캔버스에 보이는 소스 창: 넘치는 축은 크롭, 남는 축은 여백
        icw = min(disp_w, int(round(W / s)))
        ich = min(disp_h, int(round(H / s)))
        cx = int(round(px * (disp_w - icw)))
        cy = int(round(py * (disp_h - ich)))
        cx -= cx % 2
        cy -= cy % 2
        cw_, ch_ = min(_even(icw * s), W), min(_even(ich * s), H)
        padx = int(round(px * (W - cw_)))
        pady = int(round(py * (H - ch_)))
        padx -= padx % 2
        pady -= pady % 2
        g = Geometry(
            "fit",
            W,
            H,
            content_w=cw_,
            content_h=ch_,
            pad_x=padx,
            pad_y=pady,
            crop_x=cx,
            crop_y=cy,
            crop_w=icw,
            crop_h=ich,
            scale=s,
            upscaled=s > 1.0 + 1e-9,
        )
    if (g.canvas_w, g.canvas_h) != tiers[0]:
        g.tier_note = f"원본 디테일 기준으로 {g.canvas_w}×{g.canvas_h} 출력 (불필요한 업스케일 방지)"
    if g.upscaled:
        g.tier_note += f" · 최소 호환 해상도에서도 {g.scale:.2f}배 확대가 필요합니다"
    return g


def profile_grid_window(
    canvas_w: float, canvas_h: float, aspect: str = "4:5", offset: float = 0.5
) -> tuple[float, float, float, float]:
    """9:16 캔버스 안에서 프로필 그리드 썸네일이 보여 주는 영역 (x, y, w, h).
    aspect 는 '가로:세로' (예: '4:5'). 폭을 최대한 쓰고 세로 위치는 offset(0..1)로 정한다."""
    a, b = (int(v) for v in aspect.split(":"))
    gh = min(canvas_h, canvas_w * b / a)
    gw = gh * a / b
    gy = (canvas_h - gh) * min(1.0, max(0.0, offset))
    gx = (canvas_w - gw) / 2
    return gx, gy, gw, gh
