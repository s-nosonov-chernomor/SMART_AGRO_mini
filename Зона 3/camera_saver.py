# camera_saver.py
import os
import time
import glob
import cv2
import datetime as dt
import threading
from pathlib import Path
from dotenv import load_dotenv
import numpy as np
from typing import Optional, List



# ===== Загрузка .env =====
load_dotenv()

# ===== Глобальные (дефолтные) параметры =====
# Эти значения используются как "базовые" по умолчанию и могут быть переопределены для каждой камеры *_N
RTSP_DEFAULT        = os.getenv("CAMERA_RTSP_URL", "rtsp://admin:admin123@192.168.202.229:554/live/ch00_0")
SAVE_DIR_DEFAULT    = os.getenv("CAMERA_SAVE_DIR", "./camera_archive")
SAVE_EVERY_DEFAULT  = int(os.getenv("CAMERA_SAVE_EVERY_SEC", "300"))
RETAIN_DAYS_DEFAULT = float(os.getenv("CAMERA_RETAIN_DAYS", "14"))
MAX_W_DEFAULT       = int(os.getenv("CAMERA_MAX_PREVIEW_W", "1280"))
JPEG_Q_DEFAULT      = int(os.getenv("CAMERA_JPEG_QUALITY", "90"))
RTSP_TRANSPORT      = os.getenv("CAMERA_RTSP_TRANSPORT", "tcp")  # tcp | udp
WARMUP_MS_DEFAULT   = int(os.getenv("CAMERA_WARMUP_MS", "700"))

# Создадим базовую папку (если задана)
if SAVE_DIR_DEFAULT:
    try:
        Path(SAVE_DIR_DEFAULT.strip().strip('"').strip("'")).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# В camera_saver.py — ЗАМЕНИТЕ этот блок
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    f"rtsp_transport;{RTSP_TRANSPORT}|"
    "rtsp_flags;prefer_tcp|"
    "stimeout;5000000|"        # 5s
    "rw_timeout;15000000|"     # 15s
    "max_delay;500000|"        # 0.5s джиттер-буфер
    "reorder_queue_size;512|"  # дайте декодеру шанс на B-frames
    "allowed_media_types;video"
)


# ===== Утилиты окружения / пути =====
def _clean_env_val(v: str) -> str:
    """Срезает возможные кавычки и хвостовые пробелы из значений .env"""
    return v.strip().strip('"').strip("'")

def cam_env(name: str, cam: int, default: Optional[str] = None) -> Optional[str]:
    """Читает переменную окружения с приоритетом *_<cam>, иначе базовую.
       Пример: CAMERA_SAVE_DIR_2 > CAMERA_SAVE_DIR.
    """
    v = os.getenv(f"{name}_{cam}")
    if v is None:
        v = os.getenv(name, default)
    if isinstance(v, str):
        v = _clean_env_val(v)
    return v

def list_cam_ids() -> List[int]:
    """Возвращает список ID камер для запуска.
       Если CAMERA_IDS задана — используем её.
       Иначе пробуем 1..4, включая те, у которых задан RTSP.
    """
    raw = os.getenv("CAMERA_IDS")
    if raw:
        parts = raw.replace(";", ",").split(",")
        ids = []
        for x in parts:
            x = x.strip()
            if x.isdigit():
                ids.append(int(x))
        if ids:
            return ids
    # fallback: 1..4, если для камеры есть RTSP (индивидуальный или общий)
    out = []
    for i in (1, 2, 3, 4):
        if cam_env("CAMERA_RTSP_URL", i, RTSP_DEFAULT):
            out.append(i)
    return out

# ===== Время/имена файлов =====
def now() -> dt.datetime:
    return dt.datetime.now()

def ts_to_name(ts: dt.datetime) -> str:
    # YYYYMMDD_HHMMSSmmm.jpg
    return ts.strftime("%Y%m%d_%H%M%S") + f"{int(ts.microsecond/1000):03d}.jpg"

def name_to_ts(fname: str):
    base = os.path.basename(fname)
    stem, ext = os.path.splitext(base)
    if ext.lower() != ".jpg":
        return None
    try:
        date_part, time_part = stem.split("_")
        sec_dt = dt.datetime.strptime(date_part + "_" + time_part[:6], "%Y%m%d_%H%M%S")
        ms = int(time_part[6:9]) if len(time_part) >= 9 else 0
        return sec_dt + dt.timedelta(milliseconds=ms)
    except Exception:
        return None

# ===== Сохранение и ретенция =====
def save_frame_to(frame: np.ndarray, save_dir: str, max_w: int, jpeg_q: int) -> str:
    """Сохраняет кадр в указанную папку (атомарно), масштабируя по ширине при необходимости."""
    out_dir = Path(save_dir.strip().strip('"').strip("'"))
    out_dir.mkdir(parents=True, exist_ok=True)

    ts_name = ts_to_name(now())
    path = out_dir / ts_name
    tmp_path = out_dir / (ts_name + ".part")

    h, w = frame.shape[:2]
    if w > max_w:
        scale = max_w / float(w)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_q)])
    if not ok:
        raise RuntimeError("cv2.imencode('.jpg', ...) вернул False")

    try:
        with open(tmp_path, "wb") as f:
            f.write(buf.tobytes())
        os.replace(tmp_path, path)  # атомарная замена
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

    if not path.exists():
        raise FileNotFoundError(f"Файл не появился на диске: {path}")
    return str(path)

def retention_cleanup_dir(save_dir: str, retain_days: float):
    """Удаляет старые кадры в указанной папке по порогу дней."""
    cutoff = now() - dt.timedelta(days=float(retain_days))
    pattern = os.path.join(save_dir.strip().strip('"').strip("'"), "*.jpg")
    for p in glob.glob(pattern):
        ts = name_to_ts(p)
        if ts and ts < cutoff:
            try:
                os.remove(p)
            except Exception:
                pass

# ===== Захват «свежего» кадра =====
def grab_fresh_frame(rtsp_url: str, warmup_ms: int) -> Optional[np.ndarray]:
    """Открывает RTSP, делает «прожиг» потока и возвращает последний кадр."""
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        t0 = time.time()
        last = None
        reads = 0
        # Прожигаем поток по времени и количеству кадров
        while (time.time() - t0) * 1000 < warmup_ms and reads < 60:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            last = frame
            reads += 1
        # Пара дополнительных чтений
        for _ in range(3):
            ok, frame = cap.read()
            if ok and frame is not None:
                last = frame
        return last
    finally:
        try:
            cap.release()
        except Exception:
            pass

# ===== Воркер одной камеры =====
def run_camera_worker(cam_id: int):
    """Поток для одной камеры: снятие кадров по расписанию, сохранение в свою папку, ретенция."""
    # Пер-камерные параметры с фоллбэком на глобальные
    rtsp       = cam_env("CAMERA_RTSP_URL", cam_id, RTSP_DEFAULT)
    save_dir   = cam_env("CAMERA_SAVE_DIR", cam_id, os.path.join(SAVE_DIR_DEFAULT, f"cam{cam_id}"))
    save_every = int(cam_env("CAMERA_SAVE_EVERY_SEC", cam_id, str(SAVE_EVERY_DEFAULT)))
    retain_days= float(cam_env("CAMERA_RETAIN_DAYS", cam_id, str(RETAIN_DAYS_DEFAULT)))
    max_w      = int(cam_env("CAMERA_MAX_PREVIEW_W", cam_id, str(MAX_W_DEFAULT)))
    jpeg_q     = int(cam_env("CAMERA_JPEG_QUALITY", cam_id, str(JPEG_Q_DEFAULT)))
    warmup_ms  = int(cam_env("CAMERA_WARMUP_MS", cam_id, str(WARMUP_MS_DEFAULT)))

    print(f"[cam{cam_id}] RTSP={rtsp}")
    print(f"[cam{cam_id}] DIR={save_dir}, EVERY={save_every}s, RETAIN={retain_days}d, MAX_W={max_w}, JPEG_Q={jpeg_q}")

    last_cleanup = 0.0
    while True:
        try:
            frame = grab_fresh_frame(rtsp, warmup_ms)
            if frame is None:
                print(f"[cam{cam_id}] Камера недоступна, повтор через 5с")
                time.sleep(5)
                continue

            path = save_frame_to(frame, save_dir, max_w, jpeg_q)
            print(f"[cam{cam_id}] Сохранён: {path}")

            if time.time() - last_cleanup > 3600:
                retention_cleanup_dir(save_dir, retain_days)
                last_cleanup = time.time()

            time.sleep(save_every)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[cam{cam_id}] Ошибка: {e}")
            time.sleep(3)

# ===== Запуск нескольких камер =====
def run():
    cams = list_cam_ids()
    if not cams:
        print("CAMERA_IDS не заданы и не найдено ни одной камеры в .env")
        return

    threads: List[threading.Thread] = []
    for cam_id in cams:
        t = threading.Thread(target=run_camera_worker, args=(cam_id,), daemon=True)
        t.start()
        threads.append(t)

    # держим главный поток живым
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("Останавливаемся…")

if __name__ == "__main__":
    run()
