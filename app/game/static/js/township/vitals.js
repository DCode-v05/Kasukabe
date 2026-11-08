/* Health, Built, Food and Treasury — from the world, not a model.
 *
 * These four panels used to be a deterministic client-side simulation:
 * `healthSim` invented illnesses and deaths, `BUILD_PROJECTS` listed a
 * plausible build order, `foodSim` grew and ate imaginary crops, and the
 * treasury was projected from the mock clock. All four sat beside figures
 * that were real, and a modelled number next to a real one is read as real.
 *
 * Every figure below comes from /api/town/vitals, which reads world state.
 * Where the world has no answer the panel says so rather than estimating —
 * "nothing built yet" is a finding about the town; an invented build order
 * is a finding about the viewer.
 */
(function () {
  'use strict';

  var MS = 6000;
  var state = { vitals: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function empty(msg, why) {
    return '<div class="book-empty">' + esc(msg) +
      (why ? '<br><span class="dim">' + esc(why) + '</span>' : '') + '</div>';
  }
  function unavailable(d) {
    return empty('Not available.', (d && d.why) || 'no world bound');
  }
  function stat(label, value, tone) {
    return '<div class="vit-stat"><div class="vit-n ' + (tone || '') + '">' +
      esc(value) + '</div><div class="vit-l">' + esc(label) + '</div></div>';
  }

  /* Health: who is well, who is dormant, who has died — the world's own
   * counts. Dormancy matters more than illness here: it is the one death
   * path the experiment actually has (a wallet at zero), so it gets its own
   * figure rather than being folded into "unwell". */
  function healthHtml(d) {
    if (!d || !d.available) return unavailable(d);
    var h = d.health || {};
    var by = h.by_state || {};
    var states = Object.keys(by).sort(function (a, b) { return by[b] - by[a]; });
    return '<div class="vit-grid">' +
        stat('alive', h.alive || 0, 'good') +
        stat('dormant — cannot afford to think', h.dormant || 0, h.dormant ? 'warn' : '') +
        stat('died', h.dead || 0, h.dead ? 'bad' : '') +
      '</div>' +
      (states.length
        ? '<div class="vit-rows">' + states.map(function (s) {
            return '<div class="vit-row"><span>' + esc(s) + '</span>' +
              '<span class="vit-v">' + by[s] + '</span></div>';
          }).join('') + '</div>'
        : empty('No health states reported yet.'));
  }

  /* Built: what the town has actually raised. Genesis gives it businesses and
   * households; buildings are what the residents put up themselves, so zero
   * is the honest and expected early answer. */
  function builtHtml(d) {
    if (!d || !d.available) return unavailable(d);
    var b = d.built || {};
    var out = '<div class="vit-grid">' +
        stat('buildings raised', b.building_count || 0) +
        stat('businesses', b.business_count || 0) +
        stat('households', b.household_count || 0) +
      '</div>';
    if (!(b.buildings || []).length) {
      out += empty('Nothing built yet.',
        'Genesis gives the town its businesses and homes; a building here is one ' +
        'the residents raised themselves. Zero is the expected early state.');
    } else {
      out += '<div class="vit-rows">' + b.buildings.map(function (x) {
        return '<div class="vit-row"><span>' + esc(x.kind || x.building_id) + '</span>' +
          '<span class="dim">' + esc(x.owner_id || '') + '</span></div>';
      }).join('') + '</div>';
    }
    if ((b.businesses || []).length) {
      out += '<div class="vit-sub">Businesses</div><div class="vit-rows">' +
        b.businesses.map(function (x) {
          return '<div class="vit-row"><span>' + esc(x.name) +
            ' <span class="dim">' + esc(x.business_type) + '</span></span>' +
            '<span class="dim">' + esc(x.location_id || '') + '</span></div>';
        }).join('') + '</div>';
    }
    return out;
  }

  /* Food: what the town is actually holding, summed across every inventory.
   * Not a harvest model — the real stock, which is usually nothing at all
   * early on, and that is the interesting part. */
  function foodHtml(d) {
    if (!d || !d.available) return unavailable(d);
    var f = d.food || {}, held = f.holdings || {};
    var items = Object.keys(held).sort(function (a, b) { return held[b] - held[a]; });
    var total = items.reduce(function (n, k) { return n + held[k]; }, 0);
    var out = '<div class="vit-grid">' +
        stat('food held, all owners', total, total ? 'good' : 'warn') +
        stat('resource zones', (f.zones || []).length) +
      '</div>';
    out += items.length
      ? '<div class="vit-rows">' + items.map(function (k) {
          return '<div class="vit-row"><span>' + esc(k) + '</span>' +
            '<span class="vit-v">' + held[k] + '</span></div>';
        }).join('') + '</div>'
      : empty('The town is holding no food.',
          'Nobody has harvested, cooked or bought anything edible yet. The land ' +
          'is there — ' + (f.zones || []).length + ' resource zones — and the ' +
          'stock is empty, which is a fact about the run, not a missing panel.');
    return out;
  }

  /* Treasury: the ledger's own balance for the government account. */
  function treasuryHtml(d) {
    if (!d || !d.available) return unavailable(d);
    var t = d.treasury || {};
    if (t.balance_sim == null) {
      return empty('The ledger did not report a treasury balance.');
    }
    return '<div class="vit-grid">' +
        stat('treasury, sim', Number(t.balance_sim).toLocaleString()) +
      '</div>' +
      '<p class="vit-note">Every action fee in the town is paid here. It rises ' +
      'as residents work and falls as the government spends — so a treasury ' +
      'that only grows is a finding about the government, not a bug.</p>';
  }

  function render() {
    var d = state.vitals;

    /* The top bar's Alive / Deceased, from the world.
     *
     * These were written by the legacy health render off `healthSim`, which
     * still holds all 314 build-time residents - so it raced live-cast and put
     * 314 back over the real 30. That render is now switched off (its target
     * id is renamed), and these are the world's own figures. */
    if (d && d.available && d.health) {
      var a = document.getElementById('hudAlive');
      var x = document.getElementById('hudDead');
      if (a) a.textContent = d.health.alive;
      if (x) x.textContent = d.health.dead;

      /* Town wealth and treasury, from the ledger.
       *
       * The last two mock figures in the UI. `hudWealth` was summed in the
       * browser from build-time balances and showed **NaN** once the cast
       * changed under it. `hudTreasury` came from the client-side fiscal
       * projection and showed **-4,815** - the ledger's treasury was +220 at
       * the same moment, and a real treasury cannot go negative. */
      var w = document.getElementById('hudWealth');
      var t = document.getElementById('hudTreasury');
      if (w && typeof d.town_wealth_sim === 'number') {
        w.textContent = d.town_wealth_sim.toLocaleString();
      }
      if (t && d.treasury && typeof d.treasury.balance_sim === 'number') {
        t.textContent = d.treasury.balance_sim.toLocaleString();
      }
    }
    var pairs = [['vitHealth', healthHtml], ['vitBuilt', builtHtml],
                 ['vitFood', foodHtml], ['vitTreasury', treasuryHtml]];
    pairs.forEach(function (p) {
      var el = document.getElementById(p[0]);
      if (el) el.innerHTML = p[1](d);
    });
  }

  async function poll() {
    try {
      state.vitals = await fetch('/api/town/vitals', { cache: 'no-store' })
        .then(function (r) { return r.json(); });
    } catch (e) {
      state.vitals = { available: false, why: 'the viewer could not reach its own API' };
    }
    render();
  }

  function start() { poll(); setInterval(poll, MS); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }

  window.KasukabeVitals = { poll: poll, state: state };
})();
