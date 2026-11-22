"""Shopify Admin API access: reading products, building the list of changes to review, and pushing
only the changes the person approved in the app."""
import json
import math
import re
import time
import urllib.error
import urllib.request

DEFAULT_API_VERSION = "2026-07"

PRODUCTS_QUERY = """
query Products($after: String) {
  products(first: 100, after: $after) {
    nodes {
      id
      title
      status
      variants(first: 20) {
        nodes {
          id
          title
          sku
          price
          inventoryQuantity
          inventoryItem {
            id
            tracked
            inventoryLevels(first: 5) {
              nodes {
                location { id name }
                quantities(names: ["available"]) { name quantity }
              }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""

SHOP_QUERY = "query { shop { name myshopifyDomain currencyCode } }"

UPDATE_VARIANTS = """
mutation UpdateVariants($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price sku }
    userErrors { field message }
  }
}"""

SET_STOCK = """
mutation SetStock($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) {
    inventoryAdjustmentGroup { id }
    userErrors { field message code }
  }
}"""

CREATE_PRODUCT = """
mutation CreateProduct($product: ProductCreateInput!) {
  productCreate(product: $product) {
    product { id title variants(first: 1) { nodes { id } } }
    userErrors { field message }
  }
}"""


class ShopifyError(Exception):
    pass


def normalize_shop(shop):
    shop = re.sub(r"^https?://", "", (shop or "").strip()).split("/")[0]
    if shop and "." not in shop:
        shop += ".myshopify.com"
    return shop


class ShopifyClient:
    def __init__(self, shop, access_token="", client_id="", client_secret="", api_version=DEFAULT_API_VERSION,
                 base_url=None):
        self.shop = normalize_shop(shop)
        self.access_token = (access_token or "").strip()
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.api_version = api_version or DEFAULT_API_VERSION
        self.base_url = base_url or f"https://{self.shop}"  # base_url is only overridden by the tests
        self._token, self._expires = None, 0.0
        if not self.shop:
            raise ShopifyError("Add your store address in Settings first.")
        if not self.access_token and not (self.client_id and self.client_secret):
            raise ShopifyError("Add your Shopify app's Client ID and Client secret (or an Admin API access token) "
                               "in Settings first.")

    def _post(self, url, payload, headers):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              **headers})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            if exc.code in (401, 403):
                raise ShopifyError(f"Shopify refused the credentials ({exc.code}). Check the store address, the "
                                   f"app's credentials and that the app is installed on the store. {body}") from exc
            raise ShopifyError(f"Shopify returned {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ShopifyError(f"Couldn't reach Shopify ({exc.reason}). Check your internet connection and the "
                               f"store address.") from exc

    def token(self):
        if self.access_token:
            return self.access_token
        if self._token and time.time() < self._expires:
            return self._token
        data = self._post(f"{self.base_url}/admin/oauth/access_token",
                          {"client_id": self.client_id, "client_secret": self.client_secret,
                           "grant_type": "client_credentials"}, {})
        if "access_token" not in data:
            raise ShopifyError(f"Shopify didn't return an access token: {data}")
        self._token = data["access_token"]
        self._expires = time.time() + float(data.get("expires_in", 86399)) - 120
        return self._token

    def graphql(self, query, variables=None):
        for attempt in range(3):
            data = self._post(f"{self.base_url}/admin/api/{self.api_version}/graphql.json",
                              {"query": query, "variables": variables or {}},
                              {"X-Shopify-Access-Token": self.token()})
            errors = data.get("errors")
            if errors:
                throttled = any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors
                                if isinstance(e, dict))
                if throttled and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                messages = "; ".join(e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors)
                if "access" in messages.lower() and "scope" in messages.lower():
                    messages += " (add the missing access scope to your Shopify app, release a new version and approve it)"
                raise ShopifyError(messages)
            return data["data"]
        raise ShopifyError("Shopify kept rate-limiting the request. Try again in a minute.")

    def shop_info(self):
        return self.graphql(SHOP_QUERY)["shop"]

    def catalog(self):
        """All products with their variants, prices, SKUs and stock."""
        products, after = [], None
        while True:
            page = self.graphql(PRODUCTS_QUERY, {"after": after})["products"]
            for p in page["nodes"]:
                variants = []
                for v in p["variants"]["nodes"]:
                    item = v.get("inventoryItem") or {}
                    levels = [{"location_id": lv["location"]["id"], "location": lv["location"]["name"],
                               "available": next((q["quantity"] for q in lv["quantities"] if q["name"] == "available"),
                                                 None)}
                              for lv in (item.get("inventoryLevels") or {}).get("nodes", [])]
                    variants.append({"id": v["id"], "title": v["title"], "sku": v.get("sku") or "",
                                     "price": float(v["price"]), "inventory_item_id": item.get("id"),
                                     "tracked": item.get("tracked", False), "levels": levels,
                                     "total": v.get("inventoryQuantity")})
                products.append({"id": p["id"], "title": p["title"], "status": p["status"], "variants": variants})
            if not page["pageInfo"]["hasNextPage"]:
                return products
            after = page["pageInfo"]["endCursor"]

    # Writes ----------------------------------------------------------------------------------------------
    def update_variants(self, product_id, variants):
        result = self.graphql(UPDATE_VARIANTS, {"productId": product_id, "variants": variants})
        errors = result["productVariantsBulkUpdate"]["userErrors"]
        if errors:
            raise ShopifyError("; ".join(e["message"] for e in errors))

    def set_stock(self, inventory_item_id, location_id, quantity, change_from):
        result = self.graphql(SET_STOCK, {"input": {
            "name": "available", "reason": "correction",
            "quantities": [{"inventoryItemId": inventory_item_id, "locationId": location_id,
                            "quantity": quantity, "changeFromQuantity": change_from}]}})
        errors = result["inventorySetQuantities"]["userErrors"]
        if errors:
            raise ShopifyError("; ".join(e["message"] for e in errors))

    def create_draft_product(self, title, price, sku):
        result = self.graphql(CREATE_PRODUCT, {"product": {"title": title, "status": "DRAFT", "productType": "Coffee"}})
        payload = result["productCreate"]
        if payload["userErrors"]:
            raise ShopifyError("; ".join(e["message"] for e in payload["userErrors"]))
        product = payload["product"]
        variant_id = product["variants"]["nodes"][0]["id"]
        variant = {"id": variant_id, "price": f"{price:.2f}"}
        if sku:
            variant["inventoryItem"] = {"sku": sku, "tracked": True}
        self.update_variants(product["id"], [variant])
        return product["id"], variant_id


# --- Matching beans to Shopify and building the review list ----------------------------------------------

def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def _variant_index(catalog):
    index = {}
    for p in catalog:
        for v in p["variants"]:
            index[v["id"]] = (p, v)
    return index


def match_bean(bean, catalog, links):
    """Returns (product, variant, how) or (None, None, reason)."""
    index = _variant_index(catalog)
    link = links.get(bean["varietal"])
    if link == "none":
        return None, None, "not linked (you chose no Shopify product)"
    if link:
        if link in index:
            p, v = index[link]
            return p, v, "linked by you"
        return None, None, "the Shopify product you linked no longer exists"
    sku = (bean.get("sku") or "").strip().lower()
    if sku:
        hits = [(p, v) for p in catalog for v in p["variants"] if v["sku"].strip().lower() == sku]
        if len(hits) == 1:
            return hits[0][0], hits[0][1], "SKU"
    words = _words(bean["varietal"])
    if words:
        scored = sorted(((len(words & _words(p["title"])) / len(words), p) for p in catalog),
                        key=lambda s: s[0], reverse=True)
        if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            p = scored[0][1]
            if len(p["variants"]) == 1:
                return p, p["variants"][0], "name"
            return None, None, f"“{p['title']}” has several variants; pick one"
    return None, None, "no Shopify product found"


def _stock_level(variant, location_id):
    levels = variant["levels"]
    if not levels:
        return None
    if location_id:
        for lv in levels:
            if lv["location_id"] == location_id:
                return lv
    return levels[0]


def build_review(beans, catalog, prefs):
    """The comparison table plus every change the app could push. Nothing is pushed from here."""
    links = prefs.get("links", {})
    lbs_per_bag = prefs.get("lbs_per_bag")
    location_id = prefs.get("location_id") or ""
    rows, matched_ids = [], set()
    for bean in beans:
        product, variant, how = match_bean(bean, catalog, links)
        row = {"varietal": bean["varietal"], "origin": bean["origin"], "sku": bean["sku"], "msrp": bean["msrp"],
               "lbs_remaining": bean["lbs_remaining"], "match": how, "changes": []}
        if variant:
            matched_ids.add(product["id"])
            level = _stock_level(variant, location_id)
            row.update({"product_id": product["id"], "product_title": product["title"], "product_status": product["status"],
                        "variant_id": variant["id"], "variant_title": variant["title"], "shopify_sku": variant["sku"],
                        "shopify_price": variant["price"], "tracked": variant["tracked"],
                        "stock": level["available"] if level else None,
                        "location": level["location"] if level else None,
                        "location_id": level["location_id"] if level else None,
                        "inventory_item_id": variant["inventory_item_id"]})
            if round(bean["msrp"] - variant["price"], 2) != 0:
                row["changes"].append({"id": f"price|{variant['id']}", "type": "price", "label": "Price",
                                       "from": variant["price"], "to": round(bean["msrp"], 2)})
            if bean["sku"] and bean["sku"].strip() != variant["sku"].strip():
                row["changes"].append({"id": f"sku|{variant['id']}", "type": "sku", "label": "SKU",
                                       "from": variant["sku"], "to": bean["sku"].strip()})
            if lbs_per_bag and level and bean["lbs_remaining"] is not None and variant["tracked"]:
                suggested = max(0, math.floor(bean["lbs_remaining"] / float(lbs_per_bag) + 1e-9))
                if suggested != level["available"]:
                    row["changes"].append({"id": f"stock|{variant['id']}", "type": "stock", "label": "Stock",
                                           "from": level["available"], "to": suggested,
                                           "note": f"{bean['lbs_remaining']:g} lbs ÷ {float(lbs_per_bag):g} lbs per bag"})
        else:
            if how == "no Shopify product found":
                title = " - ".join(x for x in (bean["origin"], bean["varietal"]) if x)
                row["changes"].append({"id": f"create|{bean['varietal']}", "type": "create",
                                       "label": "Create draft product", "from": None,
                                       "to": f"{title} at ${bean['msrp']:.2f}" + (f", SKU {bean['sku']}" if bean["sku"] else ""),
                                       "title": title})
        rows.append(row)
    extras = [{"id": p["id"], "title": p["title"], "status": p["status"],
               "price": p["variants"][0]["price"] if p["variants"] else None}
              for p in catalog if p["id"] not in matched_ids]
    choices = [{"variant_id": v["id"], "label": p["title"] + ("" if v["title"] == "Default Title" else f" / {v['title']}")}
               for p in catalog for v in p["variants"]]
    return {"rows": rows, "extras": extras, "choices": sorted(choices, key=lambda c: c["label"].lower())}


def push(client, beans, prefs, approved):
    """Pushes only the approved changes, after re-reading Shopify to make sure each one still applies.

    approved: list of {"id": change id, "from": value the person saw, "to": value (only used for stock)}.
    Returns (results, new_links) where results is a list of dicts for the app and the sync log.
    """
    review = build_review(beans, client.catalog(), prefs)
    by_id = {}
    for row in review["rows"]:
        for change in row["changes"]:
            by_id[change["id"]] = (row, change)
        if row.get("variant_id"):  # manual stock entry is allowed on any tracked, matched variant
            by_id.setdefault(f"stock|{row['variant_id']}", (row, {"id": f"stock|{row['variant_id']}", "type": "stock",
                                                                  "label": "Stock", "from": row.get("stock")}))
    results, new_links, variant_updates = [], {}, {}

    def result(row, change, to, outcome, ok):
        results.append({"varietal": row["varietal"], "change": change["label"], "from": change.get("from"),
                        "to": to, "result": outcome, "ok": ok})

    for item in approved:
        found = by_id.get(item.get("id"))
        if not found:
            results.append({"varietal": item.get("varietal", ""), "change": item.get("id", "").split("|")[0],
                            "from": item.get("from"), "to": item.get("to"), "ok": False,
                            "result": "Skipped: no longer needed, or the bean or product changed. Review again."})
            continue
        row, change = found
        if change["type"] != "create" and _differs(change.get("from"), item.get("from")):
            result(row, change, item.get("to"), f"Skipped: Shopify now shows {change.get('from')}, not "
                                                f"{item.get('from')}. Review again.", False)
            continue
        if change["type"] in ("price", "sku"):
            v = variant_updates.setdefault(row["product_id"], {}).setdefault(row["variant_id"], {"id": row["variant_id"]})
            if change["type"] == "price":
                v["price"] = f"{change['to']:.2f}"
            else:
                v["inventoryItem"] = {"sku": change["to"]}
            v.setdefault("_pending", []).append((row, change, change["to"]))
        elif change["type"] == "stock":
            to = item.get("to") if item.get("to") is not None else change.get("to")
            try:
                to = int(to)
                if to < 0:
                    raise ValueError
            except (TypeError, ValueError):
                result(row, change, item.get("to"), "Skipped: stock must be a whole number, 0 or more.", False)
                continue
            if not row.get("location_id"):
                result(row, change, to, "Skipped: this product isn't stocked at any location in Shopify.", False)
                continue
            try:
                client.set_stock(row["inventory_item_id"], row["location_id"], to, change.get("from"))
                result(row, change, to, f"Done ({row['location']})", True)
            except ShopifyError as exc:
                result(row, change, to, f"Failed: {exc}", False)
        elif change["type"] == "create":
            bean = next(b for b in beans if b["varietal"] == row["varietal"])
            try:
                _, variant_id = client.create_draft_product(change["title"], round(bean["msrp"], 2), bean["sku"])
                new_links[row["varietal"]] = variant_id
                result(row, change, change["to"], "Done: created as a draft (hidden until you publish it in Shopify)", True)
            except ShopifyError as exc:
                result(row, change, change["to"], f"Failed: {exc}", False)

    for product_id, variants in variant_updates.items():
        pending = [p for v in variants.values() for p in v.pop("_pending")]
        try:
            client.update_variants(product_id, list(variants.values()))
            for row, change, to in pending:
                result(row, change, to, "Done", True)
        except ShopifyError as exc:
            for row, change, to in pending:
                result(row, change, to, f"Failed: {exc}", False)
    return results, new_links


def _differs(a, b):
    if a is None or b is None:
        return a != b and not (a in (None, "") and b in (None, ""))
    try:
        return round(float(a), 2) != round(float(b), 2)
    except (TypeError, ValueError):
        return str(a).strip() != str(b).strip()
