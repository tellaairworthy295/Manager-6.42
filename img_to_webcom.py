import os

from flask import request, Flask
import re
import json
import wechat_work_webhook
import urllib.parse

import requests
import time
import urllib.parse


def download_pic(panel_url, vars_dict):
    # Ensure the image directory exists
    image_dir = "images"
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)

    render_url = convert_to_render_url(panel_url, vars_dict)
    header = {"Content-Type": "application/json",
              "Authorization": "Bearer glsa_31JqYz5euv5s7t6myo9sJbKPz8YRvcU7_450eeba4"}  # 用管理员去Grafana生成API Key
    res = requests.get(render_url, headers=header)
    time_now = int(time.time())
    time_local = time.localtime(time_now)
    dt = time.strftime("%Y-%m-%d-%H-%M-%S", time_local)
    img_name = f"img{dt}.jpg"
    filename = os.path.join(image_dir, img_name)  # Save in the 'image' folder
    with open(filename, "wb") as f:
        f.write(res.content)
        return filename


def convert_to_render_url(panel_url, vars_dict):
    """
    Converts a Grafana dashboard panel URL to a renderable image URL, updating variables.

    Args:
        panel_url (str): The original panel URL.
        vars_dict (dict): A dictionary of variable names to their new values.

    Returns:
        str: The converted render URL with updated variables.
    """
    parsed_url = urllib.parse.urlparse(panel_url)
    path_parts = parsed_url.path.split('/')
    if len(path_parts) >= 4 and path_parts[1] in ['d', 'dashboard']:
        uid = path_parts[2]
        slug = path_parts[3] if len(path_parts) > 3 else ''
        new_path = f"/render/d-solo/{uid}/{slug}"
    else:
        raise ValueError(f"Invalid panel URL format: {panel_url}")

    query_params = urllib.parse.parse_qs(parsed_url.query, keep_blank_values=True)

    # --- Update Query Parameters ---
    # 1. Set timezone to 'Asia/Shanghai'
    query_params['timezone'] = ['Asia/Shanghai']

    # 2. Extract 'viewPanel' parameter to determine 'panelId'.
    view_panel_value = query_params.pop('viewPanel', [None])[0]
    if not view_panel_value:
        raise ValueError("The 'viewPanel' parameter is missing from the URL.")

    panel_id_str = view_panel_value.lstrip('panel-')
    try:
        panel_id = int(panel_id_str)
    except ValueError:
        raise ValueError(f"The 'viewPanel' value '{view_panel_value}' does not correspond to a valid numeric panel ID.")

    query_params['panelId'] = [str(panel_id)]

    # 3. Add/Update variables from the 'vars' dictionary.
    # Grafana variables in URLs have the prefix 'var-'.
    for var_name, var_value in vars_dict.items():
        grafana_var_key = f"var-{var_name}"
        query_params[grafana_var_key] = [str(var_value)]

    # 4. Add fixed width and height for rendering
    query_params['width'] = ['1000']
    query_params['height'] = ['500']

    # Reconstruct the final query string
    new_query_string = urllib.parse.urlencode(query_params, doseq=True)
    render_url = urllib.parse.urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        new_path,
        parsed_url.params,
        new_query_string,
        parsed_url.fragment
    ))

    return render_url


app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False


@app.post('/ping')
def check():
    return "ok"


@app.post('/wechat/<token>')
def webCom_msg_sender(token):
    post_data = request.get_data()
    data = json.loads(post_data)
    # commonAnnotations is a top-level object
    # (formatted_time only exists if you added it via templates)
    time_str = data['commonAnnotations']['formatted_time']
    panel_url = data['commonAnnotations']['panel_url']
    # Convert 'vars' from str to dict
    vars_str = data['commonAnnotations'].get('vars')
    vars_dict = json.loads(vars_str) if isinstance(vars_str, str) else vars_str

    # values is inside the first alert object
    value = data['alerts'][0]['values']['B']

    # commonLabels is top-level
    title = data['commonLabels']['alertname']

    # panelURL is inside the first alert object
    print(panel_url)
    print(vars)
    t1 = str(re.findall(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", time_str))
    # Connect to WeChat
    wechat = wechat_work_webhook.connect(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + token
    )

    # ---------------------------------------------------------
    # Step 1: Send Template Card (For Clickable Link & Text)
    # ---------------------------------------------------------
    card_payload = {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            # "source": {
            #     "icon_url": "https://grafana.com/static/img/grafana_icon.svg",  # Optional: Grafana Logo
            #     "desc": "Grafana Alert"
            # },
            "main_title": {
                "title": f"🚨 {title}",
                "desc": f"Time: {t1}"
            },
            "emphasis_content": {
                "title": str(value),
                "desc": "Current Value"
            },
            #"sub_title_text": "Alert Details",
            # This creates the clickable button
            "action_list": [
                {
                    "text": "View Dashboard",
                    "key": "view_dashboard",
                    "type": 1,  # Type 1 means open URL
                    "url": panel_url
                }
            ],
            # This makes the whole card clickable
            "card_action": {
                "type": 1,
                "url": panel_url
            }
        }
    }

    webhook_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={token}"
    requests.post(webhook_url, json=card_payload)

    filename = download_pic(panel_url, vars_dict)
    wechat.image(filename)
    return "OK"


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888)