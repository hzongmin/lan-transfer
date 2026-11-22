# 第三方依赖说明

这些版本来自现有 V5 打包环境。本次仅整理项目，没有升级依赖或验证重新安装。版本记录不表示其为最新版本或已通过安全审计。准备长期分发前，应单独安排依赖升级和兼容性验证。

| 依赖 | 用途 | 版本 | 上游 |
| --- | --- | --- | --- |
| qrcode | 二维码生成 | 8.2 | https://github.com/lincolnloop/python-qrcode |
| Pillow | 二维码图片、Tkinter 图像显示 | 11.3.0 | https://github.com/python-pillow/Pillow |
| cryptography | 电脑互传的可选加密 | 41.0.7 | https://github.com/pyca/cryptography |
| tkinterdnd2 | 可选文件拖拽，成品构建包含 | 0.4.3 | https://github.com/Eliav2/tkinterdnd2 |
| PyInstaller | 构建 Windows EXE | 6.19.0 | https://github.com/pyinstaller/pyinstaller |

Python 标准库还使用 Tkinter、HTTP、socket 等。具体许可和版权声明以各上游对应版本的 LICENSE 和分发包为准；本文件不是替代这些许可的授权文件，也不是成品内全部传递依赖的完整清单。

公开源码时不要把 Python 环境、第三方库源码或构建缓存一并上传。分发 EXE 时，应保留打包工具自动收集的许可文件，并根据实际打包依赖补充所需声明。
