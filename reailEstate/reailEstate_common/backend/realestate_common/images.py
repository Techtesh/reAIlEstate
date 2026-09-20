import io

import numpy as np
from PIL import Image, ImageOps


def normalized_png(data, rotation_degrees):
    image = Image.open(io.BytesIO(data)).convert("RGB")
    if rotation_degrees:
        image = image.rotate(
            rotation_degrees,
            expand=True,
            resample=Image.Resampling.BICUBIC,
            fillcolor="white",
        )
    pixels = np.asarray(image)
    content = np.any(pixels < 248, axis=2)
    ys, xs = np.nonzero(content)
    if len(xs) < 100:
        raise ValueError("LAYOUT_IMAGE_HAS_NO_CONTENT")
    padding = max(12, round(min(image.size) * 0.015))
    box = (
        max(0, int(xs.min()) - padding),
        max(0, int(ys.min()) - padding),
        min(image.width, int(xs.max()) + padding + 1),
        min(image.height, int(ys.max()) + padding + 1),
    )
    image = image.crop(box)
    margin = max(12, round(min(image.size) * 0.015))
    image = ImageOps.expand(image, border=margin, fill="white")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue(), image.size, list(box), margin
