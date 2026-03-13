"""从 JSON 角色文件迁移记忆到 ChromaDB。

使用方式：
  python -m world_engine.migrate_to_chroma

功能：
  - 扫描 data/saves/ 下所有世界存档
  - 将 characters/*.json 中的四板块数据导入各自世界的 ChromaDB
  - 自动跳过已有 chromadb/ 目录的存档（幂等）
  - 原 JSON 文件保留不删除（作为备份）
"""
import json
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def migrate_world(world_dir: Path, force: bool = False):
    """迁移单个世界存档的角色数据到 ChromaDB。"""
    from world_engine.utils import SECTION_KEYS

    chroma_dir = world_dir / "chromadb"
    chars_dir = world_dir / "characters"

    if not chars_dir.exists():
        print(f"  跳过 {world_dir.name}: 无 characters/ 目录")
        return

    char_files = list(chars_dir.glob("*.json"))
    char_files = [f for f in char_files if not f.name.startswith(".")]
    if not char_files:
        print(f"  跳过 {world_dir.name}: 无角色文件")
        return

    if chroma_dir.exists() and not force:
        print(f"  跳过 {world_dir.name}: chromadb/ 已存在（使用 --force 覆盖）")
        return

    # 初始化 ChromaDB
    import chromadb
    if chroma_dir.exists():
        import shutil
        shutil.rmtree(chroma_dir)
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name="memories",
        metadata={"hnsw:space": "cosine"},
    )

    total_entries = 0
    for char_file in sorted(char_files):
        name = char_file.stem
        with open(char_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for section in SECTION_KEYS:
            entries = data.get(section, [])
            if not entries:
                continue
            if isinstance(entries, str):
                # 旧纯文本格式
                entries = [
                    {"content": line.lstrip("- "), "ttl": "永久", "created": ""}
                    for line in entries.split("\n") if line.strip()
                ]

            ids = []
            documents = []
            metadatas = []
            for i, e in enumerate(entries):
                content = (e.get("content", "") or e.get("text", "")).strip()
                if not content:
                    continue
                entry_id = f"{name}__{section}__{i:04d}"
                ids.append(entry_id)
                documents.append(content)
                metadatas.append({
                    "character": name,
                    "section": section,
                    "ttl": e.get("ttl", "永久"),
                    "created": e.get("created", ""),
                    "hit_count": e.get("hit_count", 0),
                })

            if ids:
                collection.add(ids=ids, documents=documents, metadatas=metadatas)
                total_entries += len(ids)

        print(f"  角色 '{name}': 导入完成")

    print(f"  ✅ {world_dir.name}: 共导入 {total_entries} 条记忆")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="迁移角色记忆从 JSON 到 ChromaDB")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有的 chromadb 目录")
    parser.add_argument("--world", type=str, help="指定世界名称（默认迁移所有）")
    args = parser.parse_args()

    data_dir = project_root / "data"
    saves_dir = data_dir / "saves"

    if not saves_dir.exists():
        print("❌ 未找到 data/saves/ 目录")
        return

    worlds = []
    # 也包含 current/ 如果存在
    current_dir = data_dir / "current"
    if current_dir.exists():
        worlds.append(current_dir)

    for d in sorted(saves_dir.iterdir()):
        if d.is_dir():
            if args.world and d.name != args.world:
                continue
            worlds.append(d)

    if not worlds:
        print("❌ 未找到任何世界存档")
        return

    print(f"找到 {len(worlds)} 个世界存档:")
    for w in worlds:
        print(f"\n📂 {w.name}")
        migrate_world(w, force=args.force)

    print("\n🎉 迁移完成！原 JSON 文件已保留作为备份。")


if __name__ == "__main__":
    main()
