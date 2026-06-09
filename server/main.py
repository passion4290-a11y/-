"""
Car Motion Ambient System - WebSocket Server
전기버스 멀미 저감 앰비언트 시스템 중계/처리 서버

실행: uvicorn main:app --host 0.0.0.0 --port 8765
"""

import json
import time
import math
from collections import deque
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

app = FastAPI(title="Car Motion Ambient Server")

# HTML 파일 서빙 경로 (server/ 기준 상위 폴더)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# 칼만 필터 (1D) — 가속도 노이즈 제거
# ──────────────────────────────────────────────
class KalmanFilter1D:
    def __init__(self, process_noise: float = 0.05, measurement_noise: float = 0.3):
        self.Q = process_noise
        self.R = measurement_noise
        self.x = 0.0
        self.P = 1.0

    def update(self, measurement: float) -> float:
        P_pred = self.P + self.Q
        K = P_pred / (P_pred + self.R)
        self.x = self.x + K * (measurement - self.x)
        self.P = (1.0 - K) * P_pred
        return self.x


# ──────────────────────────────────────────────
# 모션 프로세서 — 센서값 → 앰비언트 파라미터 변환
# ──────────────────────────────────────────────
class MotionProcessor:
    # 전기버스 최대 측방 가속도 기준 (m/s²)
    LATERAL_MAX = 4.5
    FORWARD_MAX = 5.0
    # 움직임 감지 데드존 (잡음 제거)
    DEAD_ZONE = 0.08

    # 가상 엔진음 파라미터
    SPEED_MAX = 22.0        # 가상 최고 속도 (m/s, 약 80km/h)
    FRICTION = 0.35         # 가속 입력 없을 때 감속 (m/s per s)
    IDLE_RPM = 800          # 공회전 RPM
    MAX_RPM = 7200          # 최대 RPM

    def __init__(self):
        self.kf_lateral = KalmanFilter1D(process_noise=0.05, measurement_noise=0.3)
        self.kf_forward = KalmanFilter1D(process_noise=0.05, measurement_noise=0.3)
        self.lateral_history: deque[float] = deque(maxlen=15)
        self.last_processed = 0.0
        self.virtual_speed = 0.0   # 적분된 가상 속도 (m/s)

    def _apply_dead_zone(self, value: float, threshold: float) -> float:
        return 0.0 if abs(value) < threshold else value

    def _normalize(self, value: float, max_val: float) -> float:
        return max(-1.0, min(1.0, value / max_val))

    def process(self, raw: dict) -> dict:
        now = time.time()
        dt = now - self.last_processed if self.last_processed else 0.016
        self.last_processed = now

        acc = raw.get("acceleration", {}) or {}
        acc_g = raw.get("accelerationIncludingGravity", {}) or {}
        orient = raw.get("orientation", {}) or {}

        # 순수 가속도 우선, 없으면 중력 포함 사용
        lateral_raw = acc.get("x") or acc_g.get("x") or 0.0
        forward_raw = acc.get("z") or acc_g.get("z") or 0.0

        # 칼만 필터 적용
        lateral_f = self.kf_lateral.update(float(lateral_raw))
        forward_f = self.kf_forward.update(float(forward_raw))

        # 데드존 적용
        lateral_f = self._apply_dead_zone(lateral_f, self.DEAD_ZONE)
        forward_f = self._apply_dead_zone(forward_f, self.DEAD_ZONE)

        # -1 ~ 1 정규화
        lateral_norm = self._normalize(lateral_f, self.LATERAL_MAX)
        forward_norm = self._normalize(forward_f, self.FORWARD_MAX)

        self.lateral_history.append(lateral_norm)

        # 좌우 방향 판별
        direction = "neutral"
        if lateral_norm < -0.08:
            direction = "left"
        elif lateral_norm > 0.08:
            direction = "right"

        # 가감속 판별
        motion_state = "cruise"
        if forward_norm > 0.1:
            motion_state = "accelerating"
        elif forward_norm < -0.1:
            motion_state = "braking"

        # 앰비언트 색상 계산
        hue, saturation, lightness = self._compute_color(lateral_norm, forward_norm)

        # 흐름 방향 (측방 이동 반대)
        flow_offset = -lateral_norm

        # 가상 엔진음 계산
        engine = self._compute_engine(forward_f, forward_norm, dt)

        return {
            "lateral": round(lateral_norm, 4),
            "forward": round(forward_norm, 4),
            "tilt_lr": round(float(orient.get("gamma", 0) or 0), 2),
            "tilt_fb": round(float(orient.get("beta", 0) or 0), 2),
            "direction": direction,
            "motion_state": motion_state,
            "color": {"h": hue, "s": saturation, "l": lightness},
            "flow_offset": round(flow_offset, 4),
            "engine": engine,
            "timestamp": raw.get("timestamp", now * 1000),
        }

    def _compute_engine(self, forward_acc: float, forward_norm: float, dt: float) -> dict:
        """
        전후 가속도를 적분해 가상 속도를 추정하고,
        속도 + 가속(스로틀)을 합쳐 RPM / 엔진음 강도를 산출한다.
        """
        # dt 폭주 방지
        dt = max(0.001, min(0.1, dt))

        # 가속도 적분 → 가상 속도 (가속 입력 없으면 마찰로 감속)
        self.virtual_speed += forward_acc * dt
        self.virtual_speed -= self.FRICTION * dt
        self.virtual_speed = max(0.0, min(self.SPEED_MAX, self.virtual_speed))

        speed_norm = self.virtual_speed / self.SPEED_MAX            # 0~1
        throttle = max(0.0, forward_norm)                          # 가속 시 양수

        # RPM: 공회전 + 속도 기여 + 스로틀 기여
        rpm = (
            self.IDLE_RPM
            + speed_norm * (self.MAX_RPM - self.IDLE_RPM) * 0.7
            + throttle * (self.MAX_RPM - self.IDLE_RPM) * 0.3
        )
        rpm = max(self.IDLE_RPM, min(self.MAX_RPM, rpm))

        # 엔진음 강도(볼륨): 공회전 기본 + 속도 + 스로틀
        intensity = 0.12 + speed_norm * 0.55 + throttle * 0.5
        intensity = max(0.0, min(1.0, intensity))

        return {
            "rpm": round(rpm),
            "intensity": round(intensity, 4),
            "speed_kmh": round(self.virtual_speed * 3.6, 1),
            "throttle": round(throttle, 4),
        }

    def _compute_color(self, lateral: float, forward: float) -> tuple[int, int, int]:
        """
        기본 사이언(187°)을 기준으로
        - 좌 이동 → hue 감소 (초록 방향, 168~187°)
        - 우 이동 → hue 증가 (파랑 방향, 187~210°)
        - 가속     → lightness 감소 (더 깊은 톤)
        - 감속     → lightness 증가 (더 밝은 톤)
        """
        base_hue = 187
        base_sat = 95
        base_light = 32

        # lateral: -1(좌) ~ 0 ~ 1(우)
        hue_shift = lateral * 23          # 최대 ±23°
        hue = int(base_hue + hue_shift)
        hue = max(164, min(210, hue))

        # forward: 가속(양수) → 어둡게, 감속(음수) → 밝게
        light_shift = -forward * 8
        lightness = int(base_light + light_shift)
        lightness = max(24, min(44, lightness))

        # 움직임 강도에 따른 채도 소폭 조정
        intensity = min(1.0, math.sqrt(lateral**2 + forward**2))
        saturation = int(base_sat + intensity * 5)
        saturation = max(85, min(100, saturation))

        return hue, saturation, lightness


# ──────────────────────────────────────────────
# 연결 관리자
# ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.phones: list[WebSocket] = []
        self.tablets: list[WebSocket] = []

    async def connect(self, ws: WebSocket, role: str):
        await ws.accept()
        if role == "phone":
            self.phones.append(ws)
        else:
            self.tablets.append(ws)
        print(f"[+] {role} 연결됨. phones={len(self.phones)}, tablets={len(self.tablets)}")

    def disconnect(self, ws: WebSocket):
        for lst in (self.phones, self.tablets):
            if ws in lst:
                lst.remove(ws)
                break
        print(f"[-] 연결 해제. phones={len(self.phones)}, tablets={len(self.tablets)}")

    async def broadcast_tablets(self, data: dict):
        dead = []
        for ws in self.tablets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.tablets.remove(ws)

    async def broadcast_status(self):
        status = {
            "type": "status",
            "phones": len(self.phones),
            "tablets": len(self.tablets),
        }
        for ws in self.phones + self.tablets:
            try:
                await ws.send_json(status)
            except Exception:
                pass


manager = ConnectionManager()
processor = MotionProcessor()


# ──────────────────────────────────────────────
# WebSocket 엔드포인트
# ──────────────────────────────────────────────
@app.websocket("/ws/{role}")
async def websocket_endpoint(ws: WebSocket, role: str):
    if role not in ("phone", "tablet"):
        await ws.close(code=4000)
        return

    await manager.connect(ws, role)
    await manager.broadcast_status()

    try:
        while True:
            raw_text = await ws.receive_text()

            if role != "phone":
                continue

            try:
                raw_data = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            processed = processor.process(raw_data)
            processed["type"] = "motion"

            await manager.broadcast_tablets(processed)

    except WebSocketDisconnect:
        manager.disconnect(ws)
        await manager.broadcast_status()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "phones": len(manager.phones),
        "tablets": len(manager.tablets),
    }


# ──────────────────────────────────────────────
# HTML 파일 서빙 (태블릿/휴대폰 브라우저 접속용)
# ──────────────────────────────────────────────
NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

@app.get("/tablet")
async def serve_tablet():
    return FileResponse(os.path.join(BASE_DIR, "tablet", "index.html"), headers=NO_CACHE)

@app.get("/phone")
async def serve_phone():
    return FileResponse(os.path.join(BASE_DIR, "phone", "index.html"), headers=NO_CACHE)

@app.get("/monitor")
async def serve_monitor():
    return FileResponse(os.path.join(BASE_DIR, "monitor", "index.html"), headers=NO_CACHE)

@app.get("/manual")
async def serve_manual():
    return FileResponse(os.path.join(BASE_DIR, "manual.html"), headers=NO_CACHE)

@app.get("/presentation")
async def serve_presentation():
    return FileResponse(os.path.join(BASE_DIR, "presentation", "index.html"), headers=NO_CACHE)

@app.get("/")
async def serve_index():
    html = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>앰비언트 시스템</title>
  <style>
    body{font-family:-apple-system,sans-serif;background:#030a0e;color:#b2ebf2;
         display:flex;flex-direction:column;align-items:center;justify-content:center;
         height:100vh;gap:20px;margin:0}
    h1{color:#4dd0e1;font-size:1.3rem;letter-spacing:.06em}
    a{display:block;padding:14px 40px;border-radius:12px;text-decoration:none;
      font-weight:600;font-size:1rem;text-align:center;transition:transform .15s}
    a:active{transform:scale(.97)}
    .tablet{background:linear-gradient(135deg,#006064,#00838f);color:#e0f7fa}
    .phone {background:linear-gradient(135deg,#1a3040,#0d4a5a);color:#80cbc4;
            border:1px solid #1e3a4a}
    small{color:#37474f;font-size:.8rem}
  </style>
</head>
<body>
  <h1>멀미 저감 앰비언트 시스템</h1>
  <a class="tablet" href="/tablet">태블릿 화면 열기</a>
  <a class="phone"  href="/phone">휴대폰 센서 연결</a>
  <small>모든 기기에서 동일한 Wi-Fi에 연결하세요</small>
</body>
</html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=False)
