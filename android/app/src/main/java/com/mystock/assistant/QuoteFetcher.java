package com.mystock.assistant;

import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public final class QuoteFetcher {
    private QuoteFetcher() {}

    public static String httpGet(String urlStr) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(urlStr).openConnection();
        conn.setRequestMethod("GET");
        conn.setConnectTimeout(4500);
        conn.setReadTimeout(4500);
        conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36");
        conn.setRequestProperty("Accept", "application/json, text/plain, */*");
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            conn.disconnect();
            throw new Exception("HTTP " + code);
        }
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) {
                sb.append(line);
            }
        } finally {
            conn.disconnect();
        }
        return sb.toString();
    }

    public static double parseDoubleSafe(String s) {
        if (s == null) return 0.0;
        String clean = s.replace(",", "").replace("%", "").replace("+", "").trim();
        if (clean.isEmpty()) return 0.0;
        try {
            return Double.parseDouble(clean);
        } catch (Exception e) {
            return 0.0;
        }
    }

    public static JSONObject fetchSingleQuote(String symbol) {
        String sym = symbol == null ? "" : symbol.trim();
        if (sym.isEmpty()) return null;
        boolean isKr = sym.endsWith(".KS") || sym.endsWith(".KQ") || sym.matches("^\\d{6}$");
        if (isKr) {
            String code = sym.replaceAll("[^0-9]", "");
            if (code.length() != 6) return null;
            try {
                String body = httpGet("https://m.stock.naver.com/api/stock/" + code + "/basic");
                JSONObject obj = new JSONObject(body);
                double price = parseDoubleSafe(obj.optString("closePrice", ""));
                double pct = parseDoubleSafe(obj.optString("fluctuationsRatio", "0"));
                if (price > 0) {
                    JSONObject out = new JSONObject();
                    out.put("price", price);
                    out.put("ret_1d", pct);
                    return out;
                }
            } catch (Exception ignored) {
            }
            try {
                String body = httpGet("https://polling.finance.naver.com/api/realtime/domestic/stock/" + code);
                JSONObject obj = new JSONObject(body);
                JSONArray datas = obj.optJSONArray("datas");
                if (datas != null && datas.length() > 0) {
                    JSONObject d0 = datas.getJSONObject(0);
                    double price = parseDoubleSafe(d0.optString("closePriceRaw", d0.optString("closePrice", "")));
                    double pct = parseDoubleSafe(d0.optString("fluctuationsRatio", "0"));
                    if (price > 0) {
                        JSONObject out = new JSONObject();
                        out.put("price", price);
                        out.put("ret_1d", pct);
                        return out;
                    }
                }
            } catch (Exception ignored) {
            }
            return null;
        } else {
            try {
                String body = httpGet("https://query1.finance.yahoo.com/v8/finance/chart/" + Uri.encode(sym) + "?interval=1d&range=2d");
                JSONObject obj = new JSONObject(body);
                JSONObject chart = obj.optJSONObject("chart");
                JSONArray res = chart != null ? chart.optJSONArray("result") : null;
                if (res != null && res.length() > 0) {
                    JSONObject meta = res.getJSONObject(0).optJSONObject("meta");
                    if (meta != null) {
                        double price = meta.optDouble("regularMarketPrice", 0.0);
                        double prev = meta.optDouble("chartPreviousClose", meta.optDouble("previousClose", 0.0));
                        double pct = (prev > 0 && price > 0) ? ((price / prev) - 1.0) * 100.0 : 0.0;
                        if (price > 0) {
                            JSONObject out = new JSONObject();
                            out.put("price", price);
                            out.put("ret_1d", Math.round(pct * 100.0) / 100.0);
                            return out;
                        }
                    }
                }
            } catch (Exception ignored) {
            }
            return null;
        }
    }
}
