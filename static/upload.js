// Shadowrocket http-response script, with binary-body-mode=1.
(function () {
  var parts = ($argument || "").split("|");
  var base = (parts[0] || "").replace(/\/$/, "");
  var uid = parts[1] || "";
  var token = parts[2] || "";
  var region = parts[3] || "cn";
  function finish(title, message) {
    $notification.post("MySEKAI Maps", title, message);
    $done({});
  }
  if (!/^https?:\/\//.test(base) || (!/^[0-9]{6,25}$/.test(uid) || !/^[0-9a-f]{64}$/.test(token))) {
    finish("配置不完整", "请重新从网站下载同步配置。");
    return;
  }
  if (!$response.body || !$response.body.length) {
    finish("没有响应数据", "请重新进入 MySEKAI。");
    return;
  }
  $httpClient.post({
    url: base + "/api/upload",
    headers: { "Authorization": "Bearer " + token, "X-User-Id": uid, "X-Region": region,
      "X-Original-Url": $request.url, "Content-Type": "application/octet-stream" },
    body: $response.body
  }, function (error, response, body) {
    if (error) { finish("上传失败", "无法连接网站，请检查网络和同步地址。"); return; }
    var result;
    try { result = JSON.parse(body); } catch (_) { result = {}; }
    if (!response || response.status >= 400 || !result.ok) {
      finish("上传失败", result.message || "服务器未接受这次上传。");
      return;
    }
    finish("地图已更新", "打开网站即可查看最新资源。");
  });
})();
