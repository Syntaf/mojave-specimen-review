package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	s, err := openStore(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("openStore: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func TestStoreRoundTrip(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()

	if err := s.Put(ctx, "chli", "yes", "south side, by the drive", "Syntaf"); err != nil {
		t.Fatalf("put: %v", err)
	}
	all, err := s.All(ctx)
	if err != nil {
		t.Fatalf("all: %v", err)
	}
	got := all["chli"]
	if got.Verdict != "yes" || got.Note != "south side, by the drive" || got.UpdatedBy != "Syntaf" {
		t.Fatalf("round trip mismatch: %+v", got)
	}
	if got.UpdatedAt.IsZero() {
		t.Error("updated_at was not recorded")
	}
}

func TestStoreLastWriteWins(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	mustPut(t, s, "yubr", "yes", "", "grant")
	mustPut(t, s, "yubr", "maybe", "too slow growing", "partner")

	all, _ := s.All(ctx)
	if all["yubr"].Verdict != "maybe" || all["yubr"].UpdatedBy != "partner" {
		t.Fatalf("second write did not win: %+v", all["yubr"])
	}
	if len(all) != 1 {
		t.Fatalf("expected 1 row after two writes to the same plant, got %d", len(all))
	}
}

func TestStoreClearingBothFieldsDropsTheRow(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	mustPut(t, s, "latr", "yes", "keystone", "grant")
	mustPut(t, s, "latr", "", "", "grant")

	all, _ := s.All(ctx)
	if _, ok := all["latr"]; ok {
		t.Fatal("unstamping should delete the row, not leave an empty one")
	}
}

func TestStoreRejectsBadInput(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	for _, tc := range []struct{ name, id, verdict string }{
		{"bad id", "not-a-plant-id", "yes"},
		{"sql-ish id", "chli'; DROP TABLE reviews;--", "yes"},
		{"unknown verdict", "chli", "definitely"},
	} {
		if err := s.Put(ctx, tc.id, tc.verdict, "", "grant"); err == nil {
			t.Errorf("%s: expected an error, got none", tc.name)
		}
	}
}

func TestStoreTruncatesLongNotes(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	mustPut(t, s, "chli", "yes", strings.Repeat("x", maxNoteLen+500), "grant")
	all, _ := s.All(ctx)
	if len(all["chli"].Note) != maxNoteLen {
		t.Fatalf("note not truncated: got %d chars", len(all["chli"].Note))
	}
}

func TestSessionSignatureCannotBeForged(t *testing.T) {
	a := newAuth(config{clientID: "id", clientSecret: "secret", sessionKey: "k1"})
	cookie := a.sign(&User{Login: "Syntaf"})

	if u := a.verify(cookie); u == nil || u.Login != "Syntaf" {
		t.Fatal("a cookie we just signed did not verify")
	}
	if a.verify(cookie+"x") != nil {
		t.Error("a tampered signature was accepted")
	}
	other := newAuth(config{clientID: "id", clientSecret: "secret", sessionKey: "k2"})
	if other.verify(cookie) != nil {
		t.Error("a cookie signed with a different key was accepted")
	}
}

func TestAllowlistGovernsWrites(t *testing.T) {
	a := newAuth(config{clientID: "id", clientSecret: "secret", sessionKey: "k", allowed: []string{"syntaf"}})
	if !a.CanWrite(&User{Login: "Syntaf"}) {
		t.Error("allowlist should be case-insensitive")
	}
	if a.CanWrite(&User{Login: "stranger"}) {
		t.Error("a user off the allowlist must not write")
	}
	if a.CanWrite(nil) {
		t.Error("an anonymous visitor must not write")
	}

	off := newAuth(config{})
	if off.enabled() || off.CanWrite(&User{Login: "syntaf"}) {
		t.Error("with no OAuth configured the site must stay read-only")
	}
}

func TestAPIReadOnlyWithoutSignIn(t *testing.T) {
	store := testStore(t)
	mustPut(t, store, "chli", "yes", "", "grant")
	srv := routes(store, newAuth(config{}))

	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/state", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("GET /api/state: %d", rec.Code)
	}
	var state stateResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &state); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if state.CanWrite || state.User != nil {
		t.Error("an anonymous visitor should be read-only")
	}
	if state.Verdicts["chli"] != "yes" {
		t.Error("anonymous visitors should still see the shortlist")
	}

	rec = httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPut, "/api/plants/chli", strings.NewReader(`{"verdict":"no"}`))
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated PUT should be 401, got %d", rec.Code)
	}
}

func TestAPIWriteWithSession(t *testing.T) {
	store := testStore(t)
	auth := newAuth(config{clientID: "id", clientSecret: "secret", sessionKey: "k", allowed: []string{"syntaf"}})
	srv := routes(store, auth)

	req := httptest.NewRequest(http.MethodPut, "/api/plants/opba", strings.NewReader(`{"verdict":"yes","note":"the magenta one"}`))
	req.AddCookie(&http.Cookie{Name: sessionCookie, Value: auth.sign(&User{Login: "Syntaf"})})
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("authenticated PUT: %d — %s", rec.Code, rec.Body)
	}

	all, _ := store.All(context.Background())
	if all["opba"].Verdict != "yes" || all["opba"].UpdatedBy != "Syntaf" {
		t.Fatalf("write did not land: %+v", all["opba"])
	}
}

func TestStaticSiteIsServed(t *testing.T) {
	srv := routes(testStore(t), newAuth(config{}))

	for _, tc := range []struct {
		path, contains, cache string
	}{
		{"/", "Front yard", "no-cache"},
		{"/images/chli_0.webp", "", "immutable"},
	} {
		rec := httptest.NewRecorder()
		srv.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, tc.path, nil))
		if rec.Code != http.StatusOK {
			t.Errorf("GET %s: %d", tc.path, rec.Code)
			continue
		}
		if tc.contains != "" && !strings.Contains(rec.Body.String(), tc.contains) {
			t.Errorf("GET %s did not contain %q", tc.path, tc.contains)
		}
		if !strings.Contains(rec.Header().Get("Cache-Control"), tc.cache) {
			t.Errorf("GET %s cache-control = %q, want %q", tc.path, rec.Header().Get("Cache-Control"), tc.cache)
		}
	}
}

func TestHealthz(t *testing.T) {
	srv := routes(testStore(t), newAuth(config{}))
	rec := httptest.NewRecorder()
	srv.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK || rec.Body.String() != "ok" {
		t.Fatalf("healthz: %d %q", rec.Code, rec.Body.String())
	}
}

func mustPut(t *testing.T, s *Store, id, verdict, note, by string) {
	t.Helper()
	if err := s.Put(context.Background(), id, verdict, note, by); err != nil {
		t.Fatalf("put %s: %v", id, err)
	}
}
