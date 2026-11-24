package com.mystock.assistant;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 네이버 금융 실시간 국내 지수(코스피/코스닥) 및 4대 랭킹(상승·하락·거래대금·거래량) +
 * 거래정지·관리종목·투자경고/위험 위험 플래그 수집기.
 */
public final class MarketBriefingFetcher {
    private MarketBriefingFetcher() {}

    private static JSONObject fetchIndex(String idxName) {
        try {
            String body = QuoteFetcher.httpGet("https://m.stock.naver.com/api/index/" + idxName + "/basic");
            JSONObject obj = new JSONObject(body);
            JSONObject out = new JSONObject();
            out.put("name", idxName.equals("KOSPI") ? "코스피" : "코스닥");
            out.put("price", obj.optString("closePrice", "-"));
            out.put("pct", QuoteFetcher.parseDoubleSafe(obj.optString("fluctuationsRatio", "0")));
            out.put("status", obj.optString("marketStatus", ""));
            return out;
        } catch (Exception e) {
            return null;
        }
    }

    private static List<JSONObject> fetchRankBucket(String endpoint) {
        List<JSONObject> merged = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        String[] markets = new String[]{"KOSPI", "KOSDAQ"};
        for (String mkt : markets) {
            try {
                String url = "https://m.stock.naver.com/api/stocks/" + endpoint + "/" + mkt + "?page=1&pageSize=12";
                String body = QuoteFetcher.httpGet(url);
                JSONObject obj = new JSONObject(body);
                JSONArray stocks = obj.optJSONArray("stocks");
                if (stocks == null) continue;
                for (int i = 0; i < stocks.length(); i++) {
                    JSONObject st = stocks.getJSONObject(i);
                    String code = st.optString("itemCode", "").trim();
                    if (code.isEmpty() || seen.contains(code)) continue;
                    seen.add(code);

                    String name = st.optString("stockName", code);
                    double price = QuoteFetcher.parseDoubleSafe(st.optString("closePrice", "0"));
                    double pct = QuoteFetcher.parseDoubleSafe(st.optString("fluctuationsRatio", "0"));
                    double vol = QuoteFetcher.parseDoubleSafe(st.optString("accumulatedTradingVolume", "0"));
                    double valMil = QuoteFetcher.parseDoubleSafe(st.optString("accumulatedTradingValue", "0"));
                    double valEok = Math.round((valMil / 100.0) * 10.0) / 10.0;

                    // 거래정지·관리종목·투자경고/위험/단기과열 위험 신호 탐지
                    String rawStr = st.toString();
                    boolean halted = st.optBoolean("suspension", false)
                            || "SUSPENSION".equalsIgnoreCase(st.optString("marketStatus", ""))
                            || (vol == 0 && Math.abs(pct) == 0 && rawStr.contains("정지"));
                    boolean admin = st.optBoolean("administration", false)
                            || rawStr.contains("관리종목");
                    boolean warning = rawStr.contains("투자위험")
                            || rawStr.contains("투자경고")
                            || rawStr.contains("투자주의")
                            || rawStr.contains("단기과열")
                            || rawStr.contains("WARNING")
                            || rawStr.contains("RISK");

                    String riskTag = "";
                    if (halted) riskTag = "거래정지 위험";
                    else if (admin) riskTag = "관리종목 주의";
                    else if (warning) riskTag = "투자경고·과열";
                    else if (pct >= 28.5) riskTag = "상한가 과열";
                    else if (pct <= -15.0) riskTag = "급락 투매 주의";

                    JSONObject item = new JSONObject();
                    item.put("code", code);
                    item.put("symbol", code + (mkt.equals("KOSPI") ? ".KS" : ".KQ"));
                    item.put("market", mkt);
                    item.put("name", name);
                    item.put("price", price);
                    item.put("pct", pct);
                    item.put("vol", vol);
                    item.put("val_eok", valEok);
                    item.put("risk_tag", riskTag);
                    item.put("high_risk", halted || admin || warning);
                    merged.add(item);
                }
            } catch (Exception ignored) {
            }
        }
        return merged;
    }

    private static JSONArray toSortedTop(List<JSONObject> list, final String sortKey, final boolean desc, int limit) {
        Collections.sort(list, (a, b) -> {
            double va = a.optDouble(sortKey, 0.0);
            double vb = b.optDouble(sortKey, 0.0);
            return desc ? Double.compare(vb, va) : Double.compare(va, vb);
        });
        JSONArray arr = new JSONArray();
        for (int i = 0; i < Math.min(limit, list.size()); i++) {
            arr.put(list.get(i));
        }
        return arr;
    }

    public static JSONObject fetchFullLiveMarket() {
        JSONObject out = new JSONObject();
        try {
            JSONObject kospi = fetchIndex("KOSPI");
            JSONObject kosdaq = fetchIndex("KOSDAQ");
            if (kospi != null) out.put("kospi", kospi);
            if (kosdaq != null) out.put("kosdaq", kosdaq);

            out.put("up", toSortedTop(fetchRankBucket("up"), "pct", true, 12));
            out.put("down", toSortedTop(fetchRankBucket("down"), "pct", false, 10));
            out.put("value", toSortedTop(fetchRankBucket("priceTop"), "val_eok", true, 12));
            out.put("volume", toSortedTop(fetchRankBucket("quantTop"), "vol", true, 10));
            out.put("ok", true);
        } catch (Exception e) {
            try { out.put("ok", false); } catch (Exception ignored) {}
        }
        return out;
    }
}
