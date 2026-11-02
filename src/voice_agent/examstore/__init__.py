"""The centre's own exam store: exam papers, candidates and their answers, offline.

Exam packages come from the cloud (or a file) before the exam; the store holds them in a
local SQLite database, serves them to the exam engine as its platform (``LocalAdapter``),
keeps every confirmed answer and submission, and gives them back for upload afterwards.
``python -m voice_agent.examstore`` sets it up and loads packages.
"""
