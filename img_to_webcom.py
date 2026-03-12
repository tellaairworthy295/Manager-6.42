import os
import re
from datetime import datetime, timezone

from flask import request, Flask
import json
import wechat_work_webhook
import urllib.parse
import requests
import time
import jwt


def generate_grafana_jwt(org_id: str):
    """
    Generates a short-lived JWT for Grafana URL login.
    Dynamically sets the user email based on the orgId found in the URL.
    """
    try:
        with open('private.pem', 'r') as f:
            private_key = f.read()

        # Dynamically build the username and email
        # If org_id is "2", email becomes "org2@qq.com"
        username = f"wecom_viewer_org{org_id}"
        email = f"org{org_id}@qq.com"
        display_name = f"WeCom Viewer (Org {org_id})"

        payload = {
            "sub": username,
            "email": email,
            "name": display_name,
            "exp": int(time.time()) + 3600 * 24  # Token expires in 24 hours
        }

        # Sign the token using RS256 and the private key
        token = jwt.encode(payload, private_key, algorithm='RS256', headers={'kid': 'grafana-wecom-key'})
        return token
    except Exception as e:
        print(f"Error generating JWT: {e}")
        return None


def download_pic(panel_url, vars_dict):
    """Downloads a rendered image of a Grafana panel."""
    image_dir = "images"
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)

    render_url = convert_to_render_url(panel_url, vars_dict)
    header = {
        "Content-Type": "application/json",
        "Authorization": "Bearer glsa_T4bXszj3ooE1DIT7Bd5Iry4TAz0teHWq_a026340c"
    }

    # print(f"Attempting to download image from: {render_url}")
    res = requests.get(render_url, headers=header)

    if res.status_code != 200:
        print(f"Failed to download image. Status code: {res.status_code}, Response: {res.text}")
        return None

    time_now = int(time.time())
    time_local = time.localtime(time_now)
    dt = time.strftime("%Y-%m-%d-%H-%M-%S", time_local)
    img_name = f"img_{dt}.jpg"
    filename = os.path.join(image_dir, img_name)

    with open(filename, "wb") as f:
        f.write(res.content)
        # print(f"Image saved to: {filename}")
        return filename


def convert_to_render_url(panel_url, vars_dict):
    """Converts a Grafana panel URL to a renderable image URL."""
    parsed_url = urllib.parse.urlparse(panel_url)

    path_parts = parsed_url.path.split('/')
    if len(path_parts) >= 4 and path_parts[1] in ['d', 'dashboard']:
        uid = path_parts[2]
        slug = path_parts[3] if len(path_parts) > 3 else ''
        new_path = f"/render/d-solo/{uid}/{slug}"
    else:
        raise ValueError(f"Invalid panel URL format: {panel_url}")

    query_params = urllib.parse.parse_qs(parsed_url.query, keep_blank_values=True)

    query_params['timezone'] = ['Asia/Shanghai']

    view_panel_value = query_params.pop('viewPanel', [None])[0]
    if not view_panel_value:
        raise ValueError("The 'viewPanel' parameter is missing from the URL.")

    panel_id_str = view_panel_value.lstrip('panel-')
    try:
        panel_id = int(panel_id_str)
    except ValueError:
        raise ValueError(f"The 'viewPanel' value '{view_panel_value}' does not correspond to a valid numeric panel ID.")

    query_params['panelId'] = [str(panel_id)]

    if isinstance(vars_dict, dict):
        for var_name, var_value in vars_dict.items():
            grafana_var_key = f"var-{var_name}"
            query_params[grafana_var_key] = [str(var_value)]

    query_params['width'] = ['1000']
    query_params['height'] = ['500']

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


@app.post('/wechat/<token>')
def webCom_msg_sender(token):
    post_data = request.get_data()
    data = json.loads(post_data)
    # print("Received alert data:", json.dumps(data, indent=2))

    alerts = data['alerts']

    # Group alerts by their alertname and collect necessary data
    alerts_by_type = {}
    for alert in alerts:
        alert_labels = alert['labels']
        title = alert_labels['alertname']
        if title not in alerts_by_type:
            timestamp = alert['values'].get('Time')
            # Explicitly specify UTC timezone for the conversion
            dt_object = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            # Format the datetime object
            formatted_time = dt_object.strftime("%Y-%m-%d %H:%M:%S")

            # Collect unique panel_url and formatted_time for each alert type
            # In most cases, they should be the same for all alerts of one type
            alert_annotations = alert['annotations']
            panel_url = alert_annotations.get('panel_url')
            vars_str = alert_annotations.get('vars')
            vars_dict = json.loads(vars_str) if isinstance(vars_str, str) else {}
            org_id = alert['orgId']

            alerts_by_type[title] = {
                'records': [],
                'orgId': org_id,
                'panel_url': panel_url,  # Store the first encountered URL for this type
                'formatted_time': formatted_time,  # Store the first encountered time for this type
                'vars_dict': vars_dict  # Store the first encountered vars for this type
            }

        value_string = alert['valueString']
        # Regular expression to match each item in the list
        # It captures the 'var', 'labels', and 'value' parts.
        pattern = r"\[\s*var='([^']+)'[^]]*labels=({[^}]*})[^]]*value=([^\s\]]+)\s*\]"

        matches = re.findall(pattern, value_string)

        for match in matches:
            var_name, labels_str, value_str = match

            # Parse the labels string into a dictionary for better structure
            labels = {}
            if labels_str.strip() != '{}':  # If not an empty label set
                # Split by comma and then by '=' to get key-value pairs
                for pair in labels_str.strip('{}').split(','):
                    key, val = pair.split('=', 1)  # Split only on the first '=' in case the value itself has one
                    labels[key.strip()] = val.strip()

            # Attempt to convert the value string to a number (float handles scientific notation)
            try:
                value = float(value_str)
            except ValueError:
                value = value_str

            # Only append if the labels dictionary is not empty
            if labels or var_name == '最新值':
                alerts_by_type[title]['records'].append({
                    'var': var_name,
                    'labels': labels if labels else {"key": '最新值'},
                    'value': value,
                })

    # --- Build the enhanced markdown content ---
    summary_section = (
        f"> **告警总数**: `{len(alerts)}`  \n"
        f"> ---------------------------\n"
    )

    details_sections = []
    panel_urls_and_vars = []

    for i, (title, alert_data) in enumerate(alerts_by_type.items()):
        # Create a blockquote for each alert group
        section_lines = [
            f"> **类型: {title}**",
            f"> **时间: {alert_data['formatted_time']}**",
        ]

        # Format records in a more compact list
        for record in alert_data['records']:
            labels_values_str = ", ".join(record['labels'].values())
            # Use a different separator or styling for values
            section_lines.append(f"> - {labels_values_str}: <font color=\"warning\">{record['value']}</font>")

        original_url = alert_data['panel_url']
        auth_panel_url = original_url
        # if original_url:
        #     # 2. Parse the URL
        #     parsed_url = urllib.parse.urlparse(original_url)
        #     query_params = urllib.parse.parse_qs(parsed_url.query)
        #
        #     # Extract orgId from the URL (defaults to '1' if not found)
        #     # org_id = query_params.get('orgId', ['1'])[0]
        #
        #     # 3. Generate the JWT dynamically for this specific orgId
        #     grafana_jwt = generate_grafana_jwt(str(alert_data['orgId']))
        #
        #     # 4. Append the token to the URL securely
        #     if grafana_jwt:
        #         query_params['auth_token'] = [grafana_jwt]  # Add the JWT token to URL
        #         new_query = urllib.parse.urlencode(query_params, doseq=True)
        #         auth_panel_url = urllib.parse.urlunparse(parsed_url._replace(query=new_query))

        # Add link and a separator if it's not the last group
        section_lines.append(f"> 🔗 [查看图表]({auth_panel_url})")

        if i < len(alerts_by_type) - 1:  # Add a separator between groups
            section_lines.append("> ---------------------------")

        details_sections.append("\n".join(section_lines))
        panel_urls_and_vars.append((alert_data['panel_url'], alert_data['vars_dict']))

    # Combine everything
    markdown_content = summary_section + "\n".join(details_sections) + "\n"

    # Send the message
    wechat = wechat_work_webhook.connect(f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={token}")
    wechat.markdown(markdown_content)

    # Generate and send ONE image per unique chart URL found
    processed_urls = set()
    for panel_url, vars_dict in panel_urls_and_vars:
        if panel_url and panel_url not in processed_urls:
            panel_url_for_image = panel_url.replace("localhost", "10.25.116.172")
            filename = download_pic(panel_url_for_image, vars_dict)
            if filename and os.path.exists(filename):
                wechat.image(filename)
                try:
                    os.remove(filename)
                except Exception as e:
                    print(f"Failed to remove temporary image file {filename}: {e}")
            else:
                print(f"Skipping image send for URL {panel_url} due to download failure.")

            processed_urls.add(panel_url)

    return "OK"


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888)