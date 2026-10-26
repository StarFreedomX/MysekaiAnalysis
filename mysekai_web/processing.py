import io
import json
import math
import re
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import render_map

SITE_NAMES = {5: "初始空地", 6: "心愿沙滩", 7: "烂漫花田", 8: "忘却之所"}
# The renderer caches Pillow images; serialize access to its shared caches.
RENDER_LOCK = threading.Lock()


class InvalidUpload(ValueError):
    pass


def decode(body, region):
    try:
        if body.lstrip().startswith((b"{", b"\xef\xbb\xbf")):
            data = json.loads(body.decode("utf-8-sig"))
        else:
            import msgpack
            from sssekai.crypto.APIManager import decrypt, SEKAI_APIMANAGER_KEYSETS
            data = msgpack.unpackb(decrypt(body, SEKAI_APIMANAGER_KEYSETS[region]), strict_map_key=False)
    except Exception as exc:
        raise InvalidUpload("无法解密文件，请确认上传的是原始响应体，并选择正确的区服。") from exc
    if not isinstance(data, dict):
        raise InvalidUpload("响应内容必须是 MySEKAI 数据对象。")
    return data


def process(body, uid, region, original_url, assets, width):
    if original_url:
        path = urlparse(original_url).path
        match = re.fullmatch(r"/api/user/(\d+)/mysekai", path)
        if not match or match[1] != uid:
            raise InvalidUpload("抓包 URL 中的游戏 UID 与访问密钥不一致。")
    data = decode(body, region)
    updated = data.get("updatedResources")
    maps = updated.get("userMysekaiHarvestMaps") if isinstance(updated, dict) else None
    if not isinstance(maps, list) or not maps:
        raise InvalidUpload("文件中没有全量地图数据，请重新进入 MySEKAI 后上传全量响应。")

    # Only inspect owner records. Other resources may contain friends' userIds.
    owners = [data, data.get("userGamedata"), updated.get("userGamedata"), *maps]
    for owner in owners:
        if isinstance(owner, dict) and owner.get("userId") is not None and str(owner["userId"]) != uid:
            raise InvalidUpload("文件中的游戏 UID 与访问密钥不一致。")

    sites = {}
    for entry in maps:
        if not isinstance(entry, dict):
            raise InvalidUpload("地图数据格式不正确。")
        sid = entry.get("mysekaiSiteId")
        if not isinstance(sid, int) or isinstance(sid, bool):
            raise InvalidUpload("地图编号格式不正确。")
        if sid not in SITE_NAMES:
            continue
        if sid in sites:
            raise InvalidUpload("同一响应中出现了重复地图。")
        drops = entry.get("userMysekaiSiteHarvestResourceDrops")
        if not isinstance(drops, list):
            raise InvalidUpload("地图缺少资源列表。")
        diamonds = 0
        for drop in drops:
            if not isinstance(drop, dict):
                raise InvalidUpload("资源数据格式不正确。")
            try:
                quantity = int(drop.get("quantity", 0))
                resource_id = int(drop.get("resourceId", -1))
                if quantity < 0:
                    raise ValueError()
                for axis in ("positionX", "positionZ"):
                    if axis in drop and drop[axis] is not None:
                        if not math.isfinite(float(drop[axis])):
                            raise ValueError()
            except (ValueError, TypeError, OverflowError) as exc:
                raise InvalidUpload("资源数量或坐标格式不正确。") from exc
            if drop.get("resourceType") == "mysekai_material" and resource_id == 12:
                diamonds += quantity
        sites[sid] = {"id": sid, "name": SITE_NAMES[sid], "diamonds": diamonds,
                      "resource_entries": len(drops), "image_url": f"/api/maps/{sid}/image"}
    if not sites:
        raise InvalidUpload("响应中没有支持的地图，当前支持地图 5、6、7、8。")

    images = {}
    with RENDER_LOCK:
        try:
            points = render_map._extract_points(data)
            for sid in sites:
                panel, status = render_map._render_site(points, sid, str(assets), width)
                if panel is None:
                    raise RuntimeError(f"map {sid}: {status}")
                output = io.BytesIO()
                panel.save(output, format="PNG")
                images[sid] = output.getvalue()
        except (ValueError, TypeError, OverflowError) as exc:
            raise InvalidUpload("资源数据无法绘制，请检查响应内容。") from exc
    metadata = {"uid": uid, "region": region,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "sites": [sites[sid] for sid in sorted(sites)]}
    return metadata, images
