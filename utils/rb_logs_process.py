
# import sqlite3
# from fastapi import HTTPException
# import json
# from typing import Dict, Any
# from config import setup_logging

# logger = setup_logging("logs/app", "app")

    
# def process_article_data(data: Dict[str, Any]):
#     """Process article result and translation data - SYNC version."""
#     article_result = data.get("article_result", "")
#     translation_field = data.get("translation", "")

#     # Process article_result
#     if article_result and isinstance(article_result, list):
#         for a_item in article_result:
#             try:
#                 if not isinstance(a_item, dict):
#                     logger.error(f"⚠️ Error saving article_result: Expected dict, got {type(a_item)}")
#                     continue
#                 url = a_item.get("url")
#                 content = a_item.get("summary")
#                 if url is None or content is None:
#                     logger.error(f"⚠️ Error saving article_result: Missing url or summary in item {a_item}")
#                     continue
#                 update_rb_article(url, content)  # Sync database call
#             except Exception as e:
#                 logger.error(f"⚠️ Error saving article_result: {e}")
#     else:
#         if article_result:
#             logger.error(f"⚠️ Error saving article_result: Expected list for article_result, got {type(article_result)}")

#     # Process translation field
#     if translation_field:
#         for t in translation_field:
#             try:
#                 url = t.get("url")
#                 title = t.get("title", "")
#                 content = t.get("content", "")
#                 if not url or not content or "HTTP" in title or "EOF" in title:
#                     continue
#                 update_translation(url, title, content)  # Sync database call
#             except Exception as e:
#                 logger.error(f"⚠️ Error saving translation item: {e}")


# def extract_json_from_content(content: str):
#     """Extract top-level JSON safely from log file content."""
#     start_idx = content.find('{"article_result":')
#     if start_idx == -1:
#         raise HTTPException(status_code=400, detail="No article_result JSON found in log")

#     # Find matching closing brace
#     brace_count = 0
#     end_idx = None
#     for i, ch in enumerate(content[start_idx:], start_idx):
#         if ch == "{":
#             brace_count += 1
#         elif ch == "}":
#             brace_count -= 1
#             if brace_count == 0:
#                 end_idx = i + 1
#                 break

#     if not end_idx:
#         raise HTTPException(status_code=400, detail="Malformed JSON block in log")

#     json_str = content[start_idx:end_idx].strip()
    
#     try:
#         data = json.loads(json_str)
#     except json.JSONDecodeError:
#         raise HTTPException(status_code=400, detail=f"Invalid JSON in log file")
#     return data

# if __name__ == "__main__":
#     path = "rb_logs/2025-11-10-21-00-19-918.log"
#     with open(path, "r", encoding="utf-8") as f:
#         content = f.read()
#     #print(content)
#     result = extract_json_from_content(content)
#     print(result)
#     process_article_data(result)
#     #print(data)

# def update_rb_article(url, article_result):
#     conn = sqlite3.connect('scraped_data.db')
#     try:
#         cursor = conn.cursor()
#         cursor.execute(
#             '''
#             UPDATE rb_articles SET article_result = ? WHERE url = ?
#             ''',
#             (
#                 article_result,
#                 url
#             )
#         )
#         conn.commit()
#         logger.info(f"Saved {cursor.rowcount} rb workflow results to SQLite database")
#     finally:
#         conn.close()

# def update_translation(url, title, content):
#     conn = sqlite3.connect('scraped_data.db')
#     try:
#         cursor = conn.cursor()
#         cursor.execute(
#             '''
#             UPDATE rb_articles 
#             SET title_zh=?, content_zh=?, translation_status=? 
#             WHERE url=? AND translation_status = 0
#             ''',
#             (title, content, 1, url)
#         )
#         conn.commit()
        
#         logger.info(f'Successfully updated {cursor.rowcount} row(s) for URL: {url}')
            
#     except sqlite3.Error as e:
#         logger.error(f'Database error occurred: {e}')
#         conn.rollback()
#         raise
#     finally:
#         conn.close()