# `make test` runs every test in this room.
.PHONY: test test-people-page people-page
test:
	python3 -W error::ResourceWarning -m unittest discover -s tests

# `make people-page CARDS=people OUT=found` builds the People page and serves it at http://127.0.0.1:8788/ (this computer only).
# CARDS is the folder of person cards write_people.py made; OUT is the folder nightly.py writes to.
people-page:
	@test -n "$(CARDS)" -a -n "$(OUT)" || { echo "usage: make people-page CARDS=people OUT=found"; exit 2; }
	@test -d node_modules || npm install
	node dashboard/example-host/build.mjs
	node dashboard/example-host/serve.mjs --cards "$(CARDS)" --out "$(OUT)" $(if $(PORT),--port $(PORT))

# The People page's own tests (needs `npm install` once).
test-people-page:
	@test -d node_modules || npm install
	npm run test:dashboard
