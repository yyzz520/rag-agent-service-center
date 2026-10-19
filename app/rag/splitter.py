from langchain_text_splitters import RecursiveCharacterTextSplitter


def get_splitter(chunk_size: int = 1500, chunk_overlap: int = 150) -> RecursiveCharacterTextSplitter:
    """中文分块：separators 必须带中文标点，否则容易把一句话切烂。

    chunk_size 之前用 500，对财务报表这类结构密集的文档切得太碎——
    一张资产负债表被切成好几段，单段内容单薄，embedding 后离任何查询都远。
    1500 能保持一个完整表格段落，180 分块对检索也友好。
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    )
