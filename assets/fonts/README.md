# assets/fonts —— 前端中文字体

## 这里放的是什么

| 文件 | 内容 |
|:--|:--|
| `NotoSansSC-Regular.ttf` | **Noto Sans SC Regular**，随仓库分发的正文字体 |
| `OFL.txt` | SIL Open Font License 1.1 全文（该字体的授权，**随分发必须附带**） |

注册点在 `app/web/__main__.py`：`page.fonts = {"LingxiSans": "fonts/NotoSansSC-Regular.ttf"}`，
配套 `page.theme = ft.Theme(font_family="LingxiSans")` 与**绝对路径**的 `assets_dir`。

## 为什么要把它放进仓库

Flet 只打包**图标字体**（MaterialIcons / CupertinoIcons），**不带正文字体**。
Flutter web（CanvasKit）在遇到字体里没有的字形时，会**运行时**去
`fonts.gstatic.com` 拉 10.5 MB 的 Noto 回退字体。后果有两个，都不能接受：

1. **首次加载整片豆腐块** —— 拉取失败或超时就是白屏方块，且**不稳定**
   （同一页面同样参数，有时正常、有时全糊）；
2. **与产品定位直接冲突** —— 本项目的核心主张是「纯本地运行、数据不出设备」
   （需求文档 §二.2 / §模块六），而一个主张隐私原生的产品在启动时去
   Google 取字体，说不过去。

所以字体**必须本地化**。之所以直接入库而不是写下载脚本，是因为：

- 授权允许 —— Noto Sans SC 采用 **SIL OFL 1.1**，明确允许随软件再分发
  （这一点与数据集/模型权重不同，后者多数禁止再分发，见 `docs/datasets.md` §2）；
- 体量有界 —— 10.5 MB，一次性成本，且 `assets/` 下只此一份；
- **干净克隆必须能跑** —— 若靠脚本现取，源站地址带版本号（`...&v=v41`）会轮换，
  脚本迟早失效；而这套前端是要拿来演示和结题的，不能依赖「当时网络好不好」。

> `.gitattributes` 已把 `*.ttf` 标为 `binary`（`-diff -merge -text`），
> 避免 Git 尝试对字体做行尾转换或文本 diff。

## 字体信息（用于校验）

| 项 | 值 |
|:--|:--|
| 家族名 | `Noto Sans SC` |
| 子家族 | `Regular` |
| 版本 | `Version 2.004-H2;hotconv 1.0.118;makeotfexe 2.5.65603` |
| 文件大小 | `10,560,380` 字节 |
| SHA-256 | `ae82f4e2a55e1316a55bcc1d05e9555ce08d8bda07e893b486896b626fd852ff` |
| 授权 | SIL Open Font License 1.1（见 `OFL.txt`） |
| 上游 | Google Fonts 的 `ofl/notosanssc`（`fonts.google.com/specimen/Noto+Sans+SC`） |

校验：

```bash
python -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('assets/fonts/NotoSansSC-Regular.ttf').read_bytes()).hexdigest())"
```

## 换字体时要注意

1. **改了文件名就要同时改** `app/web/__main__.py` 里的 `page.fonts` 映射；
2. 换完**必须**跑一遍 `python -m app.web` 并肉眼确认页面渲染 ——
   字体加载失败**不会报错**，只会静默变成豆腐块（这是本文档存在的原因之一）；
3. 若新字体不是 OFL，请一并更新 `OFL.txt` 与上面的表格。
