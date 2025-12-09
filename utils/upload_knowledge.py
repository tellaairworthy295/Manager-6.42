import re
import requests
import json
import time
from scraper.news_scraper import get_target_articles

# ============================
# Configuration
# ============================
BASE_URL = "http://localhost"   # Dify API backend (adjust if needed)
API_KEY = "dataset-HtuwAUl1MsXZifu55RyhAzv2"     # Replace with your real API key
DATASET_ID = "1ab1e0db-09ec-4ee5-b6e6-cf0115295e69"       # Replace with your Knowledge Base (dataset) ID

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
    
def upload_new_document_by_file(FILE_PATH: str):
    """Upload and index new document file to Dify KB."""
    url = f"http://localhost/v1/datasets/{DATASET_ID}/document/create-by-file"
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
                    "separator": "\n\n",
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
                "reranking_provider_name": "langgenius/siliconflow/siliconflow",
                "reranking_model_name": "BAAI/bge-reranker-v2-m3"
            },
            "weights": {
                "weight_type": None,
                "keyword_setting": {"keyword_weight": 0.3},
                "vector_setting": {
                    "vector_weight": 0.7,
                    "embedding_model_name": "BAAI/bge-m3",
                    "embedding_provider_name": "langgenius/siliconflow/siliconflow"
                }
            },
            "top_k": 4,
            "score_threshold_enabled": False,
            "score_threshold": 0.5
        },
        "embedding_model": "BAAI/bge-m3",
        "embedding_model_provider": "langgenius/siliconflow/siliconflow"
    }

    try:
        with open(FILE_PATH, "rb") as f:
            files = {"file": f}
            data = {"data": json.dumps(metadata)}

            resp = requests.post(url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            print(f"✅ Uploaded file document: {FILE_PATH}")
            return resp.json()
    except Exception as e:
        print(f"❌ Failed to upload {FILE_PATH}: {e}")
        return None


def upload_new_document_by_text(name: str, text: str):
    """Upload and index new document from raw text."""
    url = f"http://localhost/v1/datasets/{DATASET_ID}/document/create-by-text"
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
                    "separator": "\n\n",
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
                "reranking_provider_name": "langgenius/siliconflow/siliconflow",
                "reranking_model_name": "BAAI/bge-reranker-v2-m3"
            },
            "weights": {
                "weight_type": None,
                "keyword_setting": {"keyword_weight": 0.3},
                "vector_setting": {
                    "vector_weight": 0.7,
                    "embedding_model_name": "BAAI/bge-m3",
                    "embedding_provider_name": "langgenius/siliconflow/siliconflow"
                }
            },
            "top_k": 4,
            "score_threshold_enabled": False,
            "score_threshold": 0.5
        },
        "embedding_model": "BAAI/bge-m3",
        "embedding_model_provider": "langgenius/siliconflow/siliconflow",
        "name": name,
        "text": text
    }

    try:
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        print(f"✅ Uploaded text document: {name}")
        return resp.json()
    except Exception as e:
        print(f"❌ Failed to upload {name}: {e}")
        return None


def update_dify_knowledge():
    print("Starting upload...")
    titles = []
    docs = list_documents()
    articles = get_target_articles()

    for article in articles:
        title = sanitize_filename(article.get("title"))
        content = article.get("content")
        if title and content:
            doc_name = f"{title}.txt"
            titles.append(doc_name)
            if not any(doc["name"] == doc_name for doc in docs):
                upload_new_document_by_text(name=doc_name, text=content)

    for doc in docs:
        if doc["name"] not in titles:
            delete_document(doc["id"])

    print("✅ All done!")

def sanitize_filename(name):
        # Remove or replace characters not allowed in filenames
        name = name.strip()
        name = re.sub(r'[\\/*?:"<>|]', "_", name)
        return name[:100]  # trim long names to avoid OS issues

if __name__ == "__main__":
    #upload_flow()
    list_bases()