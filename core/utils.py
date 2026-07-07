"""
Shared utility functions for image encoding and preparation.
No Streamlit or OpenAI imports — pure Python.
"""

import base64


def encode_image_to_base64_url(image_bytes: bytes, media_type: str = "image/png") -> str:
    """Convert image bytes to a base64 data URL."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{media_type};base64,{b64}"


def images_to_content_dicts(image_data_list: list, image_urls: list) -> list:
    """
    Convert uploaded image bytes or URLs into OpenAI input_image content dicts.
    Returns a list ready to be passed as the 'content' of a user message.
    """
    contents = []
    if image_data_list:
        for img_data in image_data_list:
            b64 = base64.b64encode(img_data).decode("utf-8")
            contents.append({
                "type": "input_image",
                "image_url": f"data:image/png;base64,{b64}"
            })
    elif image_urls:
        for url in image_urls:
            contents.append({
                "type": "input_image",
                "image_url": url
            })
    return contents
