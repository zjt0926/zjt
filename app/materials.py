"""Material listing, per-id access, and teacher upload with knowledge entry."""

import os
import uuid

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from .auth import login_required, role_required
from .db import (
    delete_material_cascade,
    get_knowledge_body,
    get_material,
    insert_chunks,
    insert_material_and_knowledge,
    list_materials,
)
from .knowledge import ALLOWED_EXTENSIONS, chunk_text, parse_file
from . import vectorstore

bp = Blueprint("materials", __name__)

# Cross-class access policy: return 404 for both "not found" and "wrong
# class" so the response never reveals whether another class's material
# exists. Documented in README.
CROSS_CLASS_STATUS = 404

# In-page preview shows at most this many characters; larger bodies are
# truncated with a hint to download the original file.
PREVIEW_MAX_CHARS = 100_000


@bp.route("/materials", methods=["GET"])
@login_required(api=False)
def list_materials_view():
    """Render the class material list. class_id comes ONLY from session."""
    class_id = session["class_id"]
    materials = list_materials(class_id)
    return render_template(
        "materials.html",
        materials=materials,
        role=session["role"],
        class_id=class_id,
    )


@bp.route("/api/materials", methods=["GET"])
@login_required(api=True)
def api_list_materials():
    """JSON material list filtered by session class_id."""
    # Ignore any client-supplied class query parameter — class_id is
    # always taken from the session (T4.3).
    class_id = session["class_id"]
    return jsonify({"class_id": class_id, "materials": list_materials(class_id)})


@bp.route("/materials/<int:material_id>", methods=["GET"])
@login_required(api=False)
def get_material_view(material_id: int):
    """Fetch a single material, enforcing class ownership server-side.

    Renders the parsed text inline so materials can be read in the
    browser without downloading the file. Oversized bodies are truncated.
    """
    material = get_material(material_id)
    if material is None or material["class_id"] != session["class_id"]:
        # Do not leak title/body/path of another class's material.
        return {"error": "not found"}, CROSS_CLASS_STATUS
    body = get_knowledge_body(material_id)
    truncated = False
    if body is not None and len(body) > PREVIEW_MAX_CHARS:
        body = body[:PREVIEW_MAX_CHARS]
        truncated = True
    return render_template(
        "material_detail.html", material=material, body=body, truncated=truncated
    )


@bp.route("/api/materials/<int:material_id>", methods=["GET"])
@login_required(api=True)
def api_get_material(material_id: int):
    material = get_material(material_id)
    if material is None or material["class_id"] != session["class_id"]:
        return {"error": "not found"}, CROSS_CLASS_STATUS
    return jsonify(material)


@bp.route("/materials/<int:material_id>/download", methods=["GET"])
@login_required(api=False)
def download_material(material_id: int):
    """Download a material's file, enforcing class ownership."""
    material = get_material(material_id)
    if material is None or material["class_id"] != session["class_id"]:
        return {"error": "not found"}, CROSS_CLASS_STATUS
    if not material.get("file_path") or not os.path.exists(material["file_path"]):
        return {"error": "file not available"}, 404
    return send_file(material["file_path"], as_attachment=True)


@bp.route("/materials/upload", methods=["POST"])
@role_required("teacher", api=True)
def upload_material():
    """Teacher-only upload: save file, parse, write materials + knowledge.

    class_id is ALWAYS read from the session. Any ``class_id`` in the
    form body is ignored.
    """
    class_id = session["class_id"]
    user_id = session["user_id"]

    if "file" not in request.files:
        return redirect(url_for("materials.list_materials_view", upload_error="未选择文件"))
    file = request.files["file"]
    if not file or not file.filename:
        return redirect(url_for("materials.list_materials_view", upload_error="未选择文件"))

    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return redirect(url_for("materials.list_materials_view", upload_error="不支持的文件类型，仅支持 .txt 和 .md"))

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
            return redirect(url_for("materials.list_materials_view", upload_error="上传的文件为空，请确保文件有实际内容"))
        if "unsupported extension" in msg:
            return redirect(url_for("materials.list_materials_view", upload_error="不支持的文件类型，仅支持 .txt 和 .md"))
        if "invalid utf-8" in msg:
            return redirect(url_for("materials.list_materials_view", upload_error="文件编码不是 UTF-8，请转换后重试"))
        return redirect(url_for("materials.list_materials_view", upload_error=f"文件解析失败：{msg}"))
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
        return redirect(url_for("materials.list_materials_view", upload_error="上传失败，请重试"))

    return redirect(url_for(
        "materials.list_materials_view",
        upload_ok=f"「{title}」上传成功，已索引 {indexed_chunks} 个片段",
    ))


@bp.route("/materials/<int:material_id>/delete", methods=["POST"])
@login_required(api=False)
def delete_material(material_id: int):
    """Delete a material with permission check.

    - Teacher: can delete ANY material in their class.
    - Student: can only delete materials they uploaded.
    - Cross-class: 404 (no leak).
    """
    material = get_material(material_id)
    if material is None or material["class_id"] != session["class_id"]:
        return {"error": "not found"}, 404

    role = session.get("role")
    if role != "teacher" and material.get("uploaded_by") != session.get("user_id"):
        return redirect(url_for("materials.list_materials_view",
                                delete_error="无权删除该材料"))

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

    return redirect(url_for(
        "materials.list_materials_view",
        delete_ok=f"「{material['title']}」已删除",
    ))
