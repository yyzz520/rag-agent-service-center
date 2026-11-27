import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.responses import error, ok
from app.config import settings
from app.core.quota import enforce_quota
from app.core.security import UserContext, get_current_user
from app.db import get_db
from app.models import Doc
from app.rag.vector_store import delete_by_doc_id, get_vectorstore
from app.services.access import get_or_create_default_kb, owned_doc, owned_kb, visible_doc
from app.services.audit import record as audit_record
from app.services.ingest import (
    delete_doc_files,
    parse_and_index,
    read_parsed_text,
    sanitize_file_name,
    save_upload_file,
    sniff_content,
)

router = APIRouter(prefix="/docs", tags=["文档 Doc"])


def _serialize(doc: Doc, kb_name: str | None = None) -> dict:
    data = {
        "doc_id": doc.id,
        "file_name": doc.file_name,
        "kb_id": doc.kb_id,
        "status": doc.status,
        "error_msg": doc.error_msg,
        "chunk_count": doc.chunk_count,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }
    if kb_name:
        data["kb_name"] = kb_name
    return data


@router.post("", summary="上传文档：立刻返回 doc_id + parsing，后台解析完轮询 /docs/{id} 到 ready/failed")
def upload_doc(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    kb_id: str | None = Query(default=None, description="可选：指定知识库；不填用该用户的默认知识库"),
    user: UserContext = Depends(enforce_quota),
    db: Session = Depends(get_db),
):
    try:
        file_name = sanitize_file_name(file.filename)
    except ValueError as e:
        return error(40001, str(e))
    content = file.file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        return error(40001, f"文件超过大小上限（{settings.max_upload_mb}MB）")
    if len(content) == 0:
        return error(40001, "文件内容为空")
    try:
        sniff_content(file_name, content)
    except ValueError as e:
        return error(40001, str(e))

    if kb_id:
        kb = owned_kb(db, user.user_id, kb_id)
        if kb is None:
            return error(40002, "知识库不存在或无权访问")
    else:
        kb = get_or_create_default_kb(db, user.user_id)

    # status=parsing 先行落库：先显式生成 doc.id（否则要等 flush 才知道），好把文件
    # 存到 uploads/{owner}/{doc_id}/ 路径上一并把 storage_path 写进行——不能带 None 插行
    doc = Doc(
        id=uuid.uuid4().hex,
        kb_id=kb.id,
        owner_id=user.user_id,
        file_name=file_name,
        status="parsing",
    )
    path = save_upload_file(doc, content)
    doc.storage_path = str(path)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    audit_record(db, user, "doc.upload", doc.id, file_name)
    background.add_task(parse_and_index, doc.id)
    return ok(_serialize(doc, kb_name=kb.name))


@router.get("", summary="我的文档列表（doc_id 作 chat 的 doc 参数；按最近上传排）")
def list_docs(
    kb_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="parsing|ready|failed；不填=全部"),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = select(Doc).where(Doc.owner_id == user.user_id)
    if kb_id:
        q = q.where(Doc.kb_id == kb_id)
    if status:
        q = q.where(Doc.status == status)
    rows = db.execute(q.order_by(Doc.created_at.desc())).scalars().all()
    items = [_serialize(d) for d in rows]
    return ok({"total": len(items), "items": items})


@router.get("/{doc_id}", summary="文档详情（上传后轮询这个到 ready/failed）")
def doc_detail(doc_id: str, user: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = owned_doc(db, user.user_id, doc_id)
    if doc is None:
        return error(40002, "文档不存在或无权访问")
    return ok(_serialize(doc))


@router.get("/{doc_id}/parsed", summary="查看解析后的文本（排查解析/切块问题）")
def doc_parsed(doc_id: str, user: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = visible_doc(db, user, doc_id)
    if doc is None:
        return error(40002, "文档不存在或无权访问")
    text = read_parsed_text(doc.id)
    if text is None:
        return ok({
            "doc_id": doc.id, "available": False, "text": "", "chars": 0,
            "hint": "旧文档未保留解析文本；重新入库或 make reindex 后可查看",
        })
    return ok({"doc_id": doc.id, "available": True, "text": text, "chars": len(text)})


@router.get("/{doc_id}/chunks", summary="查看该文档的父/子块（排查切块问题）")
def doc_chunks(doc_id: str, user: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = visible_doc(db, user, doc_id)
    if doc is None:
        return error(40002, "文档不存在或无权访问")
    raw = get_vectorstore().get(where={"doc_id": doc.id}, include=["documents", "metadatas"])
    ids = raw.get("ids") or []
    contents = raw.get("documents") or []
    metas = raw.get("metadatas") or []
    parents = {
        m.get("parent_id"): c
        for c, m in zip(contents, metas)
        if m.get("chunk_level") == "parent"
    }
    items = []
    for cid, content, meta in zip(ids, contents, metas):
        item = {"id": cid, "content": content, "metadata": meta}
        if meta.get("chunk_level") == "child":
            item["parent_content"] = parents.get(meta.get("parent_id"))
        items.append(item)
    items.sort(key=lambda x: (
        x["metadata"].get("chunk_level") != "parent",
        x["metadata"].get("page") or 0,
        x["id"],
    ))
    children = sum(1 for m in metas if m.get("chunk_level") == "child")
    return ok({
        "doc_id": doc.id,
        "total": len(items),
        "children": children,
        "parents": len(parents),
        "items": items,
    })


@router.get("/{doc_id}/source", summary="下载/预览原始文件（溯源对照）")
def doc_source(doc_id: str, user: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = visible_doc(db, user, doc_id)
    if doc is None:
        return error(40002, "文档不存在或无权访问")
    path = Path(doc.storage_path)
    if not path.exists():
        return error(40002, "源文件不存在")
    media, _ = mimetypes.guess_type(doc.file_name)
    return FileResponse(path, media_type=media or "application/octet-stream")


@router.delete("/{doc_id}", summary="删除文档：向量块 + 源文件 + 记录一起清")
def delete_doc(doc_id: str, user: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = owned_doc(db, user.user_id, doc_id)
    if doc is None:
        return error(40002, "文档不存在或无权访问")
    deleted = delete_by_doc_id(doc.id)
    delete_doc_files(doc)
    file_name = doc.file_name
    db.delete(doc)
    db.commit()
    audit_record(db, user, "doc.delete", doc_id, file_name)
    return ok({"doc_id": doc_id, "chunks_deleted": deleted})
