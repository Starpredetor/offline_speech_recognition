#!/usr/bin/env python3

import threading
import time
import traceback

from core.audio.input import AudioInputHandler

def test_audio_capture():
    print("[TEST] Testing audio capture directly...")

    candidates = AudioInputHandler.get_system_audio_candidates()
    if not candidates:
        print("[TEST] No system audio candidates found!")
        return

    chosen = candidates[0]
    print(f"[TEST] Using system candidate: {chosen['name']} (device_id={chosen['device_id']})")

    audio_input = AudioInputHandler(
        sample_rate=16000,
        channels=int(chosen.get("channels", 1)),
        device_id=chosen.get("device_id"),
        capture_sample_rate=int(chosen.get("sample_rate", 16000)),
        use_wasapi_loopback=bool(chosen.get("use_wasapi_loopback", False)),
    )

    chunk_count = [0]

    def test_callback(chunk: bytes) -> None:
        chunk_count[0] += 1
        if chunk_count[0] % 10 == 0:
            print(f"[TEST] Received chunk #{chunk_count[0]}, size: {len(chunk)} bytes")

    print("[TEST] Starting audio capture for 10 seconds...")

    stop_event = threading.Event()

    def stop_after_delay():
        time.sleep(10)
        stop_event.set()

    stop_thread = threading.Thread(target=stop_after_delay, daemon=True)
    stop_thread.start()

    try:
        audio_input.stream_chunks(
            callback=test_callback,
            chunk_duration_ms=400,
            stop_event=stop_event,
        )
    except Exception as e:
        print(f"[TEST] Exception: {e}")
        traceback.print_exc()

    print(f"[TEST] Capture complete. Got {chunk_count[0]} chunks total")

if __name__ == "__main__":
    test_audio_capture()
