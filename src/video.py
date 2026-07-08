import cv2
import numpy as np


def generate_frames():
    camera = cv2.VideoCapture(0)
    try:
        while True:
            success, frame = camera.read()
            if not success:
                break
            ret, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        camera.release()



def decode_frame(data: bytes) -> np.ndarray | None:
    np_frame = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(np_frame, cv2.IMREAD_COLOR)


def create_video_writer(filename: str, frame: np.ndarray, fps: int = 60):
    height, width = frame.shape[:2]
    return cv2.VideoWriter(
        filename,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )


def write_frame(writer: cv2.VideoWriter, frame: np.ndarray):
    writer.write(frame)