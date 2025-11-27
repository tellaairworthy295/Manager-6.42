import requests
import json
import time
import os

# ============================
# Configuration
# ============================
BASE_URL = "http://localhost"   # Dify API backend (adjust if needed)
API_KEY = "dataset-InMwuaX5ckVrkSFnJEKqYE8S"     # Replace with your real API key
DATASET_ID = "954235c4-8108-4512-aa5e-c70bd07e882b"       # Replace with your Knowledge Base (dataset) ID

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
    print(f"📄 Found {len(bases)} Knowledge Base(s):")
    ids = [b.get("id") for b in bases]
    print("Dataset IDs:", ids)
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
    print(f"📄 Found {len(all_docs)} existing document(s) across all pages.")
    return all_docs


def delete_document(doc_id):
    """Delete one document."""
    url = f"{BASE_URL}/v1/datasets/{DATASET_ID}/documents/{doc_id}"
    resp = requests.delete(url, headers=api_headers())
    if resp.status_code == 200:
        print(f"🗑️ Deleted document: {doc_id}")
    else:
        print(f"⚠️ Failed to delete {doc_id}: {resp.text}")

def delete_all_documents():
    """Delete all documents from dataset."""
    docs = list_documents()
    for doc in docs:
        delete_document(doc["id"])
        time.sleep(1)
    print("✅ All existing documents removed.")
    
def upload_new_document(FILE_PATH: str):
    """Upload and index new document file."""
    url = f"http://localhost/v1/datasets/{DATASET_ID}/document/create-by-file"
    headers = api_headers()

    form_data = {
        "data": json.dumps({
            "indexing_technique": "high_quality",
            "process_rule": {
                "rules": {
                    "pre_processing_rules": [
                        {"id": "remove_extra_spaces", "enabled": True},
                        {"id": "remove_urls_emails", "enabled": True}
                    ],
                    "segmentation": {        # <--- MUST include this
                        "separator": "\n\n",
                        "max_tokens": 2568
                    },
                    "parent_mode": "full-doc",
                    "subchunk_segmentation": {
                        "separator": "\n\n",
                        "max_tokens": 768
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
                    "reranking_provider_name": "langgenius/siliconflow/siliconflow",
                    "reranking_model_name": "BAAI/bge-reranker-v2-m3"
                },
                "weights": {
                    "weight_type": None,
                    "keyword_setting": {"keyword_weight": 0.3},
                    "vector_setting": {
                        "vector_weight": 0.7,
                        "embedding_model_name": "BAAI/bge-large-en-v1.5",
                        "embedding_provider_name": "langgenius/siliconflow/siliconflow"
                    }
                },
                "top_k": 10,
                "score_threshold_enabled": False,
                "score_threshold": 0.5
            },
            "embedding_model": "BAAI/bge-large-en-v1.5",
            "embedding_model_provider": "langgenius/siliconflow/siliconflow"
        })
    }


    with open(FILE_PATH, "rb") as f:
        files = {"file": (os.path.basename(FILE_PATH), f)}
        print(f"📤 Uploading {FILE_PATH}...")
        resp = requests.post(url, headers=headers, data=form_data, files=files)

    try:
        resp_json = resp.json()
    except Exception:
        raise RuntimeError(f"Upload failed: {resp.status_code} {resp.text}")

    if resp.status_code != 200 or "document" not in resp_json:
        raise RuntimeError(f"Upload failed: {resp.status_code} {resp.text}")

    doc = resp_json["document"]
    doc_name = doc.get("name")
    print(f"✅ Upload initiated for document: {doc_name}")
    return doc_name


def upload_flow(path: str = "docx"):
    print("Starting upload...")
    exist_docs = []
    docs = list_documents()
    for doc in docs:
        if any(doc["name"] == f for f in os.listdir(path)):
            exist_docs.append(doc["name"])
    for f in os.listdir(path):
        if f.lower().endswith('.docx') and f not in exist_docs:
            try:
                upload_new_document(os.path.join(path, f))
            except Exception as e:
                print(f"Failed to upload {f}: {e}")
                
    print("✅ All done!")


if __name__ == "__main__":
    upload_flow()