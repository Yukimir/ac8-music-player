# 曲库维护

发行构建会直接使用仓库中已生成的三个 JSON 文件。维护游戏版本索引时，从仓库根目录运行这里的工具。

- `route_states.py` 使用本机 `work/registry.bin`、`work/music.xml` 和 `work/sfx-names.txt` 建立 Wwise 状态名称映射。
- `build_arrangements.py` 读取上述映射和音库，生成 `library/arrangements.json`。有本机收录版匹配结果时，也会读取 `work/arrangement-reference.json`。
- `build_import_manifest.py GAME BUILD --reference-library LIBRARY` 读取本机游戏资源、`work/types.json`、`work/meta/4727.bnk` 及已有的本机提取结果，生成 `library/import.json`。参考库需包含 `media-index.json` 和 `cockpit/`。
