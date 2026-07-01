from deepface import DeepFace
import numpy as np

def generate_embedding(image_path: str, model_name: str = "Facenet512") -> list[float]:
    result = DeepFace.represent(
        img_path=image_path,
        model_name=model_name,
        enforce_detection=True
    )

    return result[0]["embedding"]


def compare_embeddings(
    embedding1: list[float],
    embedding2: list[float],
    threshold: float = 0.80,
):

    emb1 = np.asarray(embedding1, dtype=np.float32)
    emb2 = np.asarray(embedding2, dtype=np.float32)

    cosine_similarity = np.dot(emb1, emb2) / (
        np.linalg.norm(emb1) * np.linalg.norm(emb2)
    )

    similarity = float(cosine_similarity * 100)
    return {
        "verified": cosine_similarity >= threshold,
        "similarity": round(similarity, 2),
        "cosine_similarity": float(cosine_similarity),
        "threshold": threshold,
    }