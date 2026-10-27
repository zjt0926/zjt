"""Material listing, per-id access, and teacher upload with knowledge entry.

Page routes render empty HTML shells; every piece of business data is
served by the JSON endpoints, which authenticate the request via the
``Authorization: Bearer`` token. ``class_id`` is always read from the
verified token, never from the request.
"""

import os
import uuid

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    send_file,
)

from .auth import login_required, role_required
from .db import (
    delete_material_cascade,
    get_conn,
    get_knowledge_body,
    get_material,
    insert_chunks,
    insert_material_and_knowledge,
    list_materials,
)
from .knowledge import ALLOWED_EXTENSIONS, chunk_text, parse_file
from . import vectorstore
from .openapi import api_spec

bp = Blueprint("materials", __name__)

# Cross-class access policy: return 404 for both "not found" and "wrong
# class" so the response never reveals whether another class's material
# exists. Documented in README.
CROSS_CLASS_STATUS = 404

# In-page preview shows at most this many characters; larger bodies are
# truncated with a hint to download the original file.
PREVIEW_MAX_CHARS = 100_000


@bp.route("/materials", methods=["GET"])
def list_materials_view():
    """Public shell; the list itself is fetched from ``/api/materials``."""
    return render_template("materials.html")


@bp.route("/api/materials", methods=["GET"])
@login_required
@api_spec(summary="列出本班材料", tags=["材料"])
def api_list_materials():
    """JSON material list filtered by the token's class_id."""
    # Any client-supplied class query parameter is ignored — class_id is
    # always taken from the verified token.
    class_id = g.current_user["class_id"]
    return jsonify(
        {
            "class_id": class_id,
            "role": g.current_user["role"],
            "user_id": g.current_user["user_id"],
            "materials": list_materials(class_id),
        }
    )


@bp.route("/materials/<int:material_id>", methods=["GET"])
def get_material_view(material_id: int):
    """Public shell; data is fetched from ``/api/materials/<id>``."""
    return render_template("material_detail.html", material_id=material_id)


@bp.route("/api/materials/<int:material_id>", methods=["GET"])
@login_required
@api_spec(summary="查看单个材料（含正文预览）", tags=["材料"])
def api_get_material(material_id: int):
    """Single material with its parsed body, enforcing class ownership."""
    material = get_material(material_id)
    if material is None or material["class_id"] != g.current_user["class_id"]:
        # Do not leak title/body/path of another class's material.
        return {"error": "not found"}, CROSS_CLASS_STATUS
    body = get_knowledge_body(material_id)
    truncated = False
    if body is not None and len(body) > PREVIEW_MAX_CHARS:
        body = body[:PREVIEW_MAX_CHARS]
        truncated = True
    return jsonify({"material": material, "body": body, "truncated": truncated})


@bp.route("/materials/<int:material_id>/download", methods=["GET"])
@login_required
def download_material(material_id: int):
    """Download a material's file, enforcing class ownership."""
    material = get_material(material_id)
    if material is None or material["class_id"] != g.current_user["class_id"]:
        return {"error": "not found"}, CROSS_CLASS_STATUS
    if not material.get("file_path") or not os.path.exists(material["file_path"]):
        return {"error": "file not available"}, 404
    return send_file(material["file_path"], as_attachment=True)


@bp.route("/materials/upload", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="上传材料（教师）",
    tags=["材料"],
    description=(
        "multipart/form-data：file（.txt/.md）+ title（可选）。"
        "解析入库并建立 FTS/向量索引；上传后可在智能体上绑定。"
    ),
)
def upload_material():
    """Teacher-only upload: save file, parse, write materials + knowledge.

    class_id is ALWAYS read from the token. Any ``class_id`` in the form
    body is ignored.
    """
    class_id = g.current_user["class_id"]
    user_id = g.current_user["user_id"]

    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "未选择文件"}), 400

    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "不支持的文件类型，仅支持 .txt 和 .md"}), 400

    safe_name = f"{uuid.uuid4().hex}_{os.path.basename(filename)}"
    class_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], str(class_id))
    os.makedirs(class_dir, exist_ok=True)
    file_path = os.path.join(class_dir, safe_name)

    title = (request.form.get("title") or os.path.splitext(filename)[0]).strip()
    if not title:
        title = os.path.splitext(filename)[0]

    saved = False
    material_id = None
    try:
        file.save(file_path)
        saved = True
        _sz = os.path.getsize(file_path)
        current_app.logger.info(
            "upload: saved file=%s size=%d bytes", file_path, _sz
        )
        body_text = parse_file(file_path, ext)
        current_app.logger.info("upload: parsed %d chars", len(body_text))
        material_id = insert_material_and_knowledge(
            class_id=class_id,
            title=title,
            file_path=file_path,
            uploaded_by=user_id,
            body_text=body_text,
        )
        # Chunk and store in DB (for keyword search + excerpt tracing).
        chunks = chunk_text(body_text)
        insert_chunks(material_id, class_id, chunks)
        # Vector indexing for semantic search.
        indexed_chunks = vectorstore.index_material(
            material_id=material_id,
            class_id=class_id,
            title=title,
            body_text=body_text,
        )
    except ValueError as exc:
        current_app.logger.warning("upload ValueError: %s (file_size=%s)", exc, os.path.getsize(file_path) if os.path.exists(file_path) else "N/A")
        msg = str(exc)
        if "empty file" in msg:
            return jsonify({"error": "上传的文件为空，请确保文件有实际内容"}), 400
        if "unsupported extension" in msg:
            return jsonify({"error": "不支持的文件类型，仅支持 .txt 和 .md"}), 400
        if "invalid utf-8" in msg:
            return jsonify({"error": "文件编码不是 UTF-8，请转换后重试"}), 400
        return jsonify({"error": f"文件解析失败：{msg}"}), 400
    except Exception:
        current_app.logger.exception("upload failed for title=%r", title)
        if material_id is not None:
            try:
                vectorstore.remove_material(material_id)
            except Exception:
                current_app.logger.exception(
                    "failed to remove vectors for material %s", material_id
                )
            try:
                delete_material_cascade(material_id)
            except Exception:
                current_app.logger.exception(
                    "failed to roll back material %s", material_id
                )
        if saved and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        return jsonify({"error": "上传失败，请重试"}), 500

    return jsonify(
        {
            "ok": True,
            "material_id": material_id,
            "message": f"「{title}」上传成功，已索引 {indexed_chunks} 个片段",
        }
    )


@bp.route("/api/materials/<int:material_id>/reindex", methods=["POST"])
@role_required("teacher")
@api_spec(
    summary="重建材料索引（教师）",
    tags=["材料"],
    description="从源文件重新分块并重建 FTS + 向量索引。",
)
def reindex_material(material_id: int):
    """Teacher-only: re-chunk and re-index one material from its source file.

    Rebuilds the FTS rows (chunks/chunks_fts), the vector entries and the
    stored body text, so later uploads of newer content stay consistent.
    """
    material = get_material(material_id)
    if material is None or material["class_id"] != g.current_user["class_id"]:
        return {"error": "not found"}, CROSS_CLASS_STATUS

    file_path = material.get("file_path")
    ext = os.path.splitext(file_path or "")[1].lower()
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "该材料没有可用的源文件（种子数据无实际文件）"}), 400

    try:
        body_text = parse_file(file_path, ext)
    except ValueError as exc:
        return jsonify({"error": f"源文件解析失败：{exc}"}), 400

    chunks = chunk_text(body_text)
    insert_chunks(material_id, material["class_id"], chunks)
    indexed = vectorstore.index_material(
        material_id=material_id,
        class_id=material["class_id"],
        title=material["title"],
        body_text=body_text,
    )
    # Keep the stored knowledge body in sync with the re-parsed content.
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE knowledge_entries SET body_text = ? WHERE material_id = ?",
            (body_text, material_id),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify(
        {"ok": True, "chunks": len(chunks), "indexed": indexed,
         "message": f"「{material['title']}」已重建索引：{indexed} 个片段"}
    )


@bp.route("/materials/<int:material_id>/delete", methods=["POST"])
@login_required
@api_spec(
    summary="删除材料",
    tags=["材料"],
    description="教师可删本班任意材料；学生仅可删自己上传的。",
)
def delete_material(material_id: int):
    """Delete a material with permission check.

    - Teacher: can delete ANY material in their class.
    - Student: can only delete materials they uploaded.
    - Cross-class: 404 (no leak).
    """
    material = get_material(material_id)
    if material is None or material["class_id"] != g.current_user["class_id"]:
        return {"error": "not found"}, 404

    role = g.current_user.get("role")
    if role != "teacher" and material.get("uploaded_by") != g.current_user.get("user_id"):
        return jsonify({"error": "无权删除该材料"}), 403

    # Remove file from disk.
    file_path = material.get("file_path")
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            current_app.logger.warning("failed to remove file %s", file_path)

    # Remove vectors.
    try:
        vectorstore.remove_material(material_id)
    except Exception:
        current_app.logger.exception("failed to remove vectors for %s", material_id)

    # Remove DB rows (chunks + knowledge_entries cascade).
    delete_material_cascade(material_id)

    return jsonify({"ok": True, "message": f"「{material['title']}」已删除"})