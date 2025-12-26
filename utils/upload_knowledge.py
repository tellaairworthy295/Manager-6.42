import requests
import json
import time
from utils.logging_config import get_news_task_logger

# Configure loguru for upload knowledge module
logger = get_news_task_logger()

# ============================
# Configuration
# ============================
BASE_URL = "http://10.25.116.40:7090"   # Dify API backend (adjust if needed)
API_KEY = "dataset-XS82GQa1J04QacZ5iFl5QHKf"     # Replace with your real API key
DATASET_ID = "aafc9493-9f65-452a-ad3a-0f140928f21b"       # Replace with your Knowledge Base (dataset) ID

# ============================
# Helper functions
# ============================

def api_headers():
    return {"Authorization": f"Bearer {API_KEY}"}

def list_bases():
    url = f"{BASE_URL}/v1/datasets"
    resp = requests.get(url, headers=api_headers())
    resp.raise_for_status()
    bases = resp.json()
    # The API response may return a top-level list (old Dify) or dict with 'data' (new Dify)
    if isinstance(bases, dict) and "data" in bases:
        bases = bases["data"]
    logger.info(f"Found {len(bases)} Knowledge Base(s):")
    ids = [b.get("id") for b in bases]
    logger.debug(f"Dataset IDs: {ids}")
    return ids
    
def list_documents():
    """Get all documents under the dataset, iterating through all pages."""
    all_docs = []
    page = 1
    limit = 30
    while True:
        url = f"{BASE_URL}/v1/datasets/{DATASET_ID}/documents?page={page}&limit={limit}"
        resp = requests.get(url, headers=api_headers())
        resp.raise_for_status()
        result = resp.json()
        docs = result.get("data", [])
        if not docs:
            break
        all_docs.extend(docs)
        if len(docs) < limit:
            break
        page += 1
    logger.info(f"Found {len(all_docs)} existing document(s) across all pages.")
    return all_docs


def delete_document(doc_id):
    """Delete one document."""
    url = f"{BASE_URL}/v1/datasets/{DATASET_ID}/documents/{doc_id}"
    resp = requests.delete(url, headers=api_headers())
    if resp.status_code == 200:
        logger.info(f"Deleted document: {doc_id}")
    else:
        logger.warning(f"Failed to delete {doc_id}: {resp.text}")

def delete_all_documents():
    """Delete all documents from dataset."""
    docs = list_documents()
    for doc in docs:
        delete_document(doc["id"])
        time.sleep(1)
    logger.info("All existing documents removed.")
    
def upload_new_document_by_file(FILE_PATH: str):
    """Upload and index new document file to Dify KB."""
    url = f"{BASE_URL}/v1/datasets/{DATASET_ID}/document/create-by-file"
    headers = api_headers()

    # Metadata payload (same structure as text upload)
    metadata = {
        "indexing_technique": "high_quality",
        "process_rule": {
            "rules": {
                "pre_processing_rules": [
                    {"id": "remove_extra_spaces", "enabled": True},
                    {"id": "remove_urls_emails", "enabled": True}
                ],
                "segmentation": {
                    "separator": "\n\n",
                    "max_tokens": 1536
                },
                "parent_mode": "paragraph",
                "subchunk_segmentation": {
                    "separator": "\n",
                    "max_tokens": 512
                }
            },
            "mode": "hierarchical"
        },
        "doc_form": "hierarchical_model",
        "doc_language": "English",
        "retrieval_model": {
            "search_method": "hybrid_search",
            "reranking_enable": True,
            "reranking_mode": "reranking_model",
            "reranking_model": {
                "reranking_provider_name": "langgenius/tongyi/tongyi",
                "reranking_model_name": "gte-rerank-v2"
            },
            "weights": {
                "weight_type": None,
                "keyword_setting": {"keyword_weight": 0.3},
                "vector_setting": {
                    "vector_weight": 0.7,
                    "embedding_model_name": "text-embedding-v4",
                    "embedding_provider_name": "langgenius/tongyi/tongyi"
                }
            },
            "top_k": 3,
            "score_threshold_enabled": False,
            "score_threshold": 0.5
        },
        "embedding_model": "text-embedding-v4",
        "embedding_model_provider": "langgenius/tongyi/tongyi"
    }

    try:
        with open(FILE_PATH, "rb") as f:
            files = {"file": f}
            data = {"data": json.dumps(metadata)}

            resp = requests.post(url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            logger.info(f"Uploaded file document: {FILE_PATH}")
            return resp.json()
    except Exception as e:
        logger.error(f"Failed to upload {FILE_PATH}: {e}")
        return None


def _upload_new_document_by_text(name: str, text: str, source: str):
    """Upload and index new document from raw text."""
    url = f"{BASE_URL}/v1/datasets/{DATASET_ID}/document/create-by-text"
    headers = api_headers()

    payload = {
        "indexing_technique": "high_quality",
        "process_rule": {
            "rules": {
                "pre_processing_rules": [
                    {"id": "remove_extra_spaces", "enabled": True},
                    {"id": "remove_urls_emails", "enabled": True}
                ],
                "segmentation": {
                    "separator": "\n\n",
                    "max_tokens": 1536
                },
                "parent_mode": "paragraph",
                "subchunk_segmentation": {
                    "separator": "\n",
                    "max_tokens": 512
                }
            },
            "mode": "hierarchical"
        },
        "doc_form": "hierarchical_model",
        "doc_language": "English",
        "retrieval_model": {
            "search_method": "hybrid_search",
            "reranking_enable": True,
            "reranking_mode": "reranking_model",
            "reranking_model": {
                "reranking_provider_name": "langgenius/tongyi/tongyi",
                "reranking_model_name": "gte-rerank-v2"
            },
            "weights": {
                "weight_type": None,
                "keyword_setting": {"keyword_weight": 0.3},
                "vector_setting": {
                    "vector_weight": 0.7,
                    "embedding_model_name": "text-embedding-v4",
                    "embedding_provider_name": "langgenius/tongyi/tongyi"
                }
            },
            "top_k": 4,
            "score_threshold_enabled": False,
            "score_threshold": 0.5
        },
        "embedding_model": "text-embedding-v4",
        "embedding_model_provider": "langgenius/tongyi/tongyi",
        "name": name,
        "text": text
    }

    try:
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        logger.info(f"Uploaded text document: {name}")
        return resp.json()
    except:
        raise Exception("Error while uploading, retrying")

def _get_document(doc_id):
    url = f"{BASE_URL}/v1/datasets/{DATASET_ID}/documents/{doc_id}"
    resp = requests.get(url, headers=api_headers())
    resp.raise_for_status()
    return resp.json()

def _wait_for_indexing(doc_id, timeout=300, interval=5):
    import time
    start = time.time()
    while time.time() - start < timeout:
        doc = _get_document(doc_id)
        if doc["indexing_status"] == "completed":
            return True
        elif doc["indexing_status"] == "error":
            return False
        time.sleep(interval)
    return False

import time

def upload_dify_knowledge(all_uploads: list[dict], date_str: str, max_retries: int = 3, retry_delay: int = 5):
    docs = list_documents()
    existing_names = {doc["name"] for doc in docs}
    logger.info("Starting upload...")

    for upload in all_uploads:
        content = "\n\n".join(upload.get("content") or [])
        source = upload.get('source')
        title = f"{source}_{date_str}"
        doc_name = f"{title}.txt"

        if not content or doc_name in existing_names:
            continue

        retries = 0
        while retries < max_retries:
            try:
                result = _upload_new_document_by_text(name=doc_name, text=content, source=source)
                doc = result.get("document")

                # Poll until indexing completes
                if _wait_for_indexing(doc["id"]):
                    break
                else:
                    raise Exception("Indexing failed")
            except Exception as e:
                retries += 1
                logger.warning(f"Upload failed for {doc_name}, attempt {retries}/{max_retries}: {e}")
                if retries >= max_retries:
                    logger.error(f"Giving up on {doc_name} after {max_retries} retries")
                else:
                    time.sleep(retry_delay)

def clean_dify_knowledge():
    docs = list_documents()
    from datetime import datetime
    twelve_hours_ago = datetime.now().timestamp() - 24.5 * 3600
    for doc in docs:
        # Delete docs where indexing failed
        if doc.get("indexing_status") == "error":
            delete_document(doc["id"])
        # Delete docs created more than 24 hours ago
        elif doc.get("created_at") is not None and float(doc["created_at"]) < twelve_hours_ago:
            delete_document(doc["id"])
    logger.info("Removal done!")


if __name__ == "__main__":
    #upload_flow()
    print(list_documents())