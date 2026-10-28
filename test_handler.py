"""phase 2 step 3 test: call the cloud front door on the laptop,
the same way Lambda will, and save the image it sends back."""
import base64
import json

from lambda_handler import lambda_handler

event = {
    "player_id": 656941,
    "name": "Kyle Schwarber",
    "fielders": ["Andrew Pinckney", "Jacob Young", "Dylan Crews"],
    "park": "nationals",
    "mode": "min_runs",
}

response = lambda_handler(event, None)
print("status:", response["statusCode"])

data = json.loads(response["body"])
if "image_png_base64" in data:
    png = base64.b64decode(data.pop("image_png_base64"))
    with open("plots/test_handler.png", "wb") as f:
        f.write(png)
    print(f"image: {len(png):,} bytes -> plots/test_handler.png")

print(json.dumps(data, indent=2))