const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export function getApiBase() {
  return API_BASE;
}

export async function checkApiHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchDocuments() {
  const res = await fetch(`${API_BASE}/documents`);
  if (!res.ok) throw new Error(`Failed to fetch documents: ${res.statusText}`);
  return res.json();
}

export async function fetchDocument(id) {
  const res = await fetch(`${API_BASE}/documents/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch document ${id}: ${res.statusText}`);
  return res.json();
}

export async function fetchDocumentFacts(id) {
  const res = await fetch(`${API_BASE}/documents/${id}/facts`);
  if (!res.ok) throw new Error(`Failed to fetch facts for document ${id}: ${res.statusText}`);
  return res.json();
}

export async function uploadDocument(file) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/documents`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Upload failed: ${res.statusText}`);
  }
  return res.json();
}

export function fetchPageImage(docId, pageIndex) {
  return `${API_BASE}/documents/${docId}/pages/${pageIndex}/image`;
}

export async function fetchEvidenceBBox(docId, pageIndex, evidenceText) {
  const url = `${API_BASE}/documents/${docId}/pages/${pageIndex}/evidence-bbox?text=${encodeURIComponent(evidenceText)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch bbox: ${res.statusText}`);
  return res.json();
}

export async function runCompare(docIds = null) {
  const body = docIds ? JSON.stringify({ document_ids: docIds }) : JSON.stringify({});
  const res = await fetch(`${API_BASE}/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (!res.ok) throw new Error(`Comparison failed: ${res.statusText}`);
  return res.json();
}

export async function fetchRelationships(type = null) {
  const url = type && type !== "all"
    ? `${API_BASE}/relationships?type=${encodeURIComponent(type)}`
    : `${API_BASE}/relationships`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch relationships: ${res.statusText}`);
  return res.json();
}

export async function fetchGraph(docId = null, relationshipType = null) {
  const params = new URLSearchParams();
  if (docId) params.append("document_id", docId);
  if (relationshipType && relationshipType !== "all") params.append("relationship_type", relationshipType);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`${API_BASE}/graph${qs}`);
  if (!res.ok) throw new Error(`Failed to fetch graph data: ${res.statusText}`);
  return res.json();
}

export async function adjudicateRelationship(relationshipId, data) {
  const res = await fetch(`${API_BASE}/relationships/${relationshipId}/adjudicate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Adjudication failed: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchTrajectories(refresh = false) {
  const url = refresh ? `${API_BASE}/synthesis/trajectories?refresh=true` : `${API_BASE}/synthesis/trajectories`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch trajectories: ${res.statusText}`);
  return res.json();
}


