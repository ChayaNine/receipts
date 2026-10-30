from app.documents import citation_hints, clean_text, locate, split_sentences
from app.grounding import find_quote, select_passages
from app.llm import parse_json, strip_thinking
from app.samples import SAMPLES


def test_sentences_do_not_break_inside_legal_citations():
    text = clean_text(SAMPLES[0]["text"])
    sentences = [text[s:e] for s, e in split_sentences(text)]
    assert any(s.startswith("In Mata v. Avianca, Inc., 678 F. Supp. 3d 443 (S.D.N.Y. 2023), the court imposed") for s in sentences)
    assert any(s.startswith("Varghese v. China Southern Airlines Co., Ltd., 925 F.3d 1339") for s in sentences)


def test_locate_exact_normalized_and_fuzzy():
    text = "Intro. The court said “no” to the motion in 2023. Outro."
    assert locate("The court said “no” to the motion in 2023.", text) is not None
    s, e = locate('The court said "no" to the motion in 2023.', text)  # straight quotes vs curly
    assert text[s:e].startswith("The court said")
    s, e = locate("The court said no to that motion in 2023", text)  # paraphrase-ish
    assert "motion" in text[s:e]
    assert locate("Completely unrelated sentence about penguins.", text) is None


def test_citation_hints_find_urls_dois_and_reference_list():
    hints = citation_hints(clean_text(SAMPLES[2]["text"]))
    assert "10.1038/s41539-019-0055-z" in hints["dois"]
    assert "Rasch, B., & Born, J. (2013)" in hints["reference_list"]
    blog = citation_hints(SAMPLES[1]["text"])
    assert "https://arxiv.org/abs/1706.03762" in blog["urls"]


def test_find_quote_exact_fuzzy_and_missing():
    source = "We propose a new simple network architecture, the Transformer, based solely on attention mechanisms."
    assert find_quote("the Transformer, based solely on attention mechanisms", source)["found"]
    assert find_quote("the Transformer, based solely upon attention mechanisms", source)["found"]  # one word off
    assert not find_quote("the Transformer, based mostly on recurrent networks", source)["found"]
    assert not find_quote("", source)["found"]


def test_select_passages_prefers_relevant_chunks():
    filler = "\n\n".join(f"Paragraph {i} talks about weather, gardening and soup recipes in detail." * 6 for i in range(30))
    source = filler + "\n\nThe model achieves 28.4 BLEU on the WMT 2014 English-to-German translation task.\n\n" + filler
    picked = select_passages(source, "Transformer scored 41.8 BLEU on WMT 2014 English-to-German", k=2, max_chars=1500)
    assert "28.4 BLEU" in picked


def test_parse_json_handles_reasoning_fences_and_trailing_commas():
    assert parse_json('<think>let me reason {"no": 1}</think>{"verdict": "supported"}') == {"verdict": "supported"}
    assert parse_json('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert parse_json('Sure! Here you go: {"claims": [{"claim": "x \\"y\\" }"}]} hope it helps') == {"claims": [{"claim": 'x "y" }'}]}
    assert strip_thinking("reasoning...</think>answer") == "answer"
