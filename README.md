# MySEKAI Maps

MySEKAI 资源地图网站。支持上传原始响应或已解密 JSON，查看和下载地图。

## 运行

```bash
cp .env.example .env
docker compose up -d --build
```

打开 `http://127.0.0.1:3939`。数据保存在 Compose 的 `data` 卷中，重建容器不会删除数据。

```bash
docker compose logs --tail 80
docker compose stop
```

手机接入时，在 `.env` 中设置 `BIND_IP=0.0.0.0`，把 `PUBLIC_BASE_URL` 改为手机能访问的地址，例如 `http://192.168.1.10:3939`，再执行 `docker compose up -d`。公网使用 HTTPS 反向代理，将 `PUBLIC_BASE_URL` 设置为站点的 HTTPS 地址。

## UID 和密码

网页分别填写游戏 UID 和 6 位数字密码。UID 为 6～25 位数字。

浏览器计算 `token = SHA-256(UID + "_" + 密码)`，输出 64 位小写十六进制字符串。上传、查询和下载图片都发送 `Authorization: Bearer <token>` 和 `X-User-Id: <UID>`，不发送明文密码。网页不使用 localStorage 或 Cookie 保存凭据。

每组 UID 和密码对应一份独立数据，只保留最近一次成功上传。更换密码会进入另一份数据，不迁移旧数据，也不需要注册账号。

SQLite 直接使用这个哈希作为数据索引，保存 UID、上传时间、原始文件、地图摘要和 PNG。这个索引与上一版的 `SHA-256(uid_password)` 相同，已有地图无需迁移。地图全部生成后才在一个事务中替换，失败时保留上次结果。

哈希是可直接使用的上传和查询凭据。公网使用 HTTPS。接口默认按连接 IP 每分钟限制 120 次请求。

## 手机自动上传

在网页输入 UID 和密码，点击“配置自动同步”，下载并导入 Shadowrocket 模块。模块仅匹配国服的全量 MySEKAI 响应，不采集 suite 数据。

旧版模块需要重新下载。启用模块并安装、信任 HTTPS 解密证书后，进入游戏的 MySEKAI。脚本把原始响应直接发送到 `/api/upload`，成功后显示通知。模块包含 UID 和哈希凭据，请自行保存；脚本没有离线缓存或自动补传。

手动上传可以使用 `.bin` 原始响应或已解密 JSON。普通代理日志、截图和只含请求头的文件不能生成地图。响应应包含 `updatedResources.userMysekaiHarvestMaps`；服务显示本次响应中包含的地图，不与旧响应合并。对于原始响应，按游戏区服解密。地图归属记录或抓包 URL 中的 UID 必须与 X-User-Id 一致。

## 失败上传记录

已经收到的非空上传内容如果解密、校验、渲染或入库失败，会保存到数据库同级的 `failed_uploads/` 目录。Docker 中是 `/data/failed_uploads/`，使用现有数据卷持久保存。

每次失败生成同名文件：

- `.bin`：收到的原始字节，不做编码转换。
- `.json`：UID、UTC 时间、区服、错误原因、异常类型、实际和预期字节数、文件 SHA-256。

错误响应增加 `archive_id`，容器日志也会记录该编号和具体失败原因。归档不记录 Authorization 或哈希凭据。数据库写入失败时，仍会尝试独立保存文件；归档写入失败会明确记录错误，不返回虚假的归档编号。

连接中断时保留已经读到的内容。鉴权失败、超过大小限制等尚未接收请求体的情况不保存 bin。成功请求仍按原方式保存到 SQLite，不额外生成失败文件。

```bash
docker compose -p mysekai-web exec web ls -lh /data/failed_uploads
docker compose -p mysekai-web logs --tail 100 web
```

失败文件不会自动清理，也不会通过网页提供下载。升级前未保存的失败请求无法补回，需要重新上传。

## HTTP 接口

| 方法与路径 | 功能 | 鉴权 |
| --- | --- | --- |
| `GET /` | 网页 | 无 |
| `GET /healthz` | 健康检查 | 无 |
| `GET /api/config` | 公开同步地址和上传大小限制 | 无 |
| `POST /api/upload` | 接收原始二进制或 JSON | UID + Bearer 哈希 |
| `GET /api/maps` | 查询最近一次地图摘要 | UID + Bearer 哈希 |
| `GET /api/maps/{site_id}/image` | 下载地图 PNG | UID + Bearer 哈希 |
| `GET /api/module` | 下载含当前上传凭据的 Shadowrocket 模块 | UID + Bearer 哈希 |
| `GET /upload.js` | Shadowrocket 上传脚本 | 无 |

地图编号为 `5` 初始空地、`6` 心愿沙滩、`7` 烂漫花田、`8` 忘却之所。

上传请求体直接放文件字节，不使用 multipart。`X-Region` 可选，支持 `cn/jp/tw/en/kr`；`X-Original-Url` 可选，Shadowrocket 脚本会自动填写。

```bash
MYSEKAI_UID=123456789
MYSEKAI_TOKEN=你的64位哈希值
curl --fail-with-body http://127.0.0.1:3939/api/upload \
  -H "Authorization: Bearer $MYSEKAI_TOKEN" \
  -H "X-User-Id: $MYSEKAI_UID" \
  -H 'Content-Type: application/octet-stream' \
  -H 'X-Region: cn' \
  --data-binary @mysekai.bin

curl --fail-with-body http://127.0.0.1:3939/api/maps \
  -H "Authorization: Bearer $MYSEKAI_TOKEN" \
  -H "X-User-Id: $MYSEKAI_UID"
```

上传及查询的成功响应：

```json
{
  "ok": true,
  "data": {
    "uid": "123456789",
    "region": "cn",
    "updated_at": "2026-10-26T10:00:00+00:00",
    "sites": [
      {
        "id": 5,
        "name": "初始空地",
        "diamonds": 2,
        "resource_entries": 8,
        "image_url": "/api/maps/5/image"
      }
    ]
  }
}
```

图片地址也需要相同的 Authorization 和 X-User-Id 请求头。网页通过 fetch 读取图片，再生成临时 Blob URL 展示和下载。

错误返回 `{"ok": false, "error": "错误代码", "message": "提示"}`。常见状态为 `401` 上传凭据格式错误或 UID 不一致、`404` 当前凭据没有数据、`413` 文件过大、`422` 解密或数据校验失败、`429` 请求过多、`503` 上传处理繁忙。上传失败不会返回成功状态。

## 配置

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PORT` | `3939` | Compose 宿主机端口；直接运行时为服务端口 |
| `BIND_IP` | `127.0.0.1` | Compose 宿主机监听地址 |
| `HOST` | `127.0.0.1` | Python 直接运行时的监听地址；容器内为 `0.0.0.0` |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:3939` | 模块里的上传和脚本地址，只填写 origin |
| `DATABASE_PATH` | `data/mysekai.sqlite3` | 直接运行时的 SQLite 文件；容器内为 `/data/mysekai.sqlite3` |
| `API_REGION` | `cn` | 未传 X-Region 时的区服 |
| `MAX_UPLOAD_BYTES` | `100000000` | 上传大小上限 |
| `MAP_IMAGE_WIDTH` | `1024` | 地图输出宽度 |
| `REQUESTS_PER_MINUTE` | `120` | 每个连接 IP 每分钟的受保护接口请求上限 |

## 开发

Python 3.11，依赖见 `requirements.txt`。前端为原生 HTML、CSS 和 JavaScript，无需 Node 构建。服务使用 Python 标准库、SQLite、Pillow 和 sssekai。地图渲染保留了原项目的底图、资源映射和坐标校准参数。

sssekai 的 PyPI 包已不可用，依赖固定到现存镜像仓库中原版 0.7.62 的提交 `adae5a4f923813ec7a4b6eab005e75044355458d`。ARM Linux 构建 UnityPy 需要编译器，Dockerfile 已包含。

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m mysekai_web
python -m unittest discover -s tests -v
node tests/test_auth.mjs
```

直接运行 Python 时不会自动读取 `.env`；请在启动前导出所需环境变量。Compose 会读取 `.env`。浏览器哈希使用本地 JavaScript 实现，局域网 HTTP 也可使用。Node 只用于运行哈希和脚本测试，不是网站运行依赖。

```text
mysekai_web/   HTTP 服务、SQLite 存储、数据处理、地图渲染
static/        网页和 Shadowrocket 上传脚本
assets/        地图底图、资源图标、资源映射
tests/        API、解密、数据隔离和地图测试
```

原有 QQ/NapCat 通知、LangBot 插件、玩家信息卡、旧接收器和 Windows 启动脚本已移除。
