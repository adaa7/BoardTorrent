# `web_modes` 说明

`web_modes` 位于 `config.json` 中，用来配置“注释→网页”解析规则。每个模式都是一个字典，程序会按顺序尝试匹配，成功后在右侧 WebView 中打开具体页面。

## 字段解释

| 字段名      | 作用 |
| ----------- | ---- |
| `name`      | 模式名称，用于工具栏下拉框和设置界面显示。 |
| `pattern`   | 正则表达式，用来匹配 qBittorrent 注释里的内容；支持命名捕获组。 |
| `template`  | URL 模板，使用 `str.format` 语法。内置 `value`（整个匹配结果）和所有命名捕获组。 |
| `description` | 纯展示用说明文字，方便在设置界面确认模式用途。 |
| `cookie`    | 访问该站点时附带的 Cookie，支持多个键值对 `key=value; foo=bar`。 |

> 备注：若开启“网页缩小自适应”，页面会自动按当前窗口宽度等比缩放并隐藏横向滚动条。

## 示例

```json
{
  "web_modes": [
    {
      "name": "KamePT",
      "pattern": "https?://kamept\\.com/details\\.php\\?id=\\d+",
      "template": "{value}",
      "description": "注释里直接填 KamePT 详情链接",
      "cookie": "uid=xxxx; passkey=yyyy"
    },
    {
      "name": "M-Team",
      "pattern": "(?P<tid>\\d+)",
      "template": "https://kp.m-team.cc/detail/{tid}",
      "description": "注释仅写种子 ID，由模板自动拼接",
      "cookie": ""
    }
  ]
}
```

## 建议

1. **正则测试**  
   可以在设置界面或在线工具里先测试 `pattern` 是否能正确匹配注释，避免启动后提示“未匹配到可用模式”。

2. **多站点组合**  
   如果一个注释中可能包含多个不同站点链接，可按优先级排列 `web_modes`；程序会自上而下尝试。

3. **Cookie 管理**  
   - 访问需要登录的站点时，把浏览器网络面板里整行 Cookie 复制到 `cookie` 字段。  
   - Cookie 与 qBittorrent 会话无关，只在 WebView 请求页面时使用。

4. **分类过滤**  
   与 `web_modes` 无直接关联，但可结合“分类下拉框”限制展示的种子数量，减少页面加载压力。

