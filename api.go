package main

import (
	"encoding/json"
	"log"
	"net/http"
)

// API is the whole write surface: read the shortlist, stamp one plant.
type API struct {
	store *Store
	auth  *Auth
}

type stateResponse struct {
	User     *User             `json:"user"`
	CanWrite bool              `json:"canWrite"`
	Verdicts map[string]string `json:"verdicts"`
	Notes    map[string]string `json:"notes"`
	By       map[string]string `json:"by"`
}

func (a *API) getState(w http.ResponseWriter, r *http.Request) {
	reviews, err := a.store.All(r.Context())
	if err != nil {
		log.Printf("read state: %v", err)
		http.Error(w, "could not read the shortlist", http.StatusInternalServerError)
		return
	}

	user := a.auth.User(r)
	resp := stateResponse{
		User:     user,
		CanWrite: a.auth.CanWrite(user),
		Verdicts: map[string]string{},
		Notes:    map[string]string{},
		By:       map[string]string{},
	}
	for id, rev := range reviews {
		if rev.Verdict != "" {
			resp.Verdicts[id] = rev.Verdict
		}
		if rev.Note != "" {
			resp.Notes[id] = rev.Note
		}
		if rev.UpdatedBy != "" {
			resp.By[id] = rev.UpdatedBy
		}
	}
	writeJSON(w, http.StatusOK, resp)
}

func (a *API) putPlant(w http.ResponseWriter, r *http.Request) {
	user := a.auth.User(r)
	if !a.auth.CanWrite(user) {
		// 401 rather than 403 even for a signed-in non-allowlisted user: the
		// client's only useful move either way is to offer sign-in again.
		writeJSON(w, http.StatusUnauthorized, map[string]string{
			"error": "sign in with an approved GitHub account to stamp plants",
		})
		return
	}

	var body struct {
		Verdict string `json:"verdict"`
		Note    string `json:"note"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 8<<10)).Decode(&body); err != nil {
		http.Error(w, "malformed request body", http.StatusBadRequest)
		return
	}

	id := r.PathValue("id")
	if err := a.store.Put(r.Context(), id, body.Verdict, body.Note, user.Login); err != nil {
		log.Printf("put %s: %v", id, err)
		http.Error(w, "could not save that stamp", http.StatusBadRequest)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"ok": "1", "by": user.Login})
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("write json: %v", err)
	}
}
