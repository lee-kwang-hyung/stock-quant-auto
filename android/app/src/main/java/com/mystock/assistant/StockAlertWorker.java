package com.mystock.assistant;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

public class StockAlertWorker extends Worker {
    public static final String CHANNEL_ID = "stock_alerts";
    public static final String PREF_NAME = "mystock_alerts_pref";
    public static final String KEY_RULES = "alert_rules_json";
    public static final String KEY_SENT_PREFIX = "sent_";

    public StockAlertWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    public static void ensureChannel(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
                NotificationChannel ch = new NotificationChannel(
                        CHANNEL_ID,
                        "주식 매매 타이밍 알림",
                        NotificationManager.IMPORTANCE_HIGH
                );
                ch.setDescription("목표 매도가·손절선·매수 구간 도달 시 알림을 보냅니다.");
                nm.createNotificationChannel(ch);
            }
        }
    }

    public static void postNotification(Context ctx, String title, String body, int notifyId) {
        ensureChannel(ctx);
        Intent intent = new Intent(ctx, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent pi = PendingIntent.getActivity(ctx, notifyId, intent, flags);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(ctx, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(true)
                .setContentIntent(pi);

        NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) {
            nm.notify(notifyId, builder.build());
        }
    }

    @NonNull
    @Override
    public Result doWork() {
        Context ctx = getApplicationContext();
        SharedPreferences prefs = ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE);
        String raw = prefs.getString(KEY_RULES, "");
        if (raw == null || raw.isEmpty()) {
            return Result.success();
        }

        SimpleDateFormat sdf = new SimpleDateFormat("yyyyMMdd", Locale.US);
        sdf.setTimeZone(TimeZone.getTimeZone("Asia/Seoul"));
        String todayKey = sdf.format(new Date());

        try {
            JSONObject cfg = new JSONObject(raw);
            if (!cfg.optBoolean("enabled", true)) {
                return Result.success();
            }
            boolean onTarget = cfg.optBoolean("onTarget", true);
            boolean onStop = cfg.optBoolean("onStop", true);
            boolean onBuy = cfg.optBoolean("onBuy", true);
            boolean onMove = cfg.optBoolean("onMove", true);

            JSONArray items = cfg.optJSONArray("items");
            if (items == null) return Result.success();

            for (int i = 0; i < items.length(); i++) {
                JSONObject it = items.getJSONObject(i);
                String sym = it.optString("symbol", "");
                String name = it.optString("name", sym);
                if (sym.isEmpty()) continue;

                JSONObject q = QuoteFetcher.fetchSingleQuote(sym);
                if (q == null) continue;
                double price = q.optDouble("price", 0.0);
                double ret1d = q.optDouble("ret_1d", 0.0);
                if (price <= 0) continue;

                boolean isUs = !(sym.endsWith(".KS") || sym.endsWith(".KQ") || sym.matches("^\\d{6}$"));
                String pStr = isUs
                        ? String.format(Locale.US, "$%.2f", price)
                        : String.format(Locale.US, "%,d원", Math.round(price));

                double t1 = it.optDouble("target1", 0.0);
                double stop = it.optDouble("stop", 0.0);
                double buyHigh = it.optDouble("buyHigh", 0.0);
                double buyLow = it.optDouble("buyLow", 0.0);
                boolean owned = it.optBoolean("owned", false);

                String triggerType = null;
                String title = null;
                String body = null;

                if (onTarget && t1 > 0 && price >= t1) {
                    triggerType = "target";
                    title = "💰 " + name + " 목표 매도가 도달!";
                    body = "현재가 " + pStr + " — 절반(50%) 수익 실현 타이밍을 확인하세요.";
                } else if (onStop && stop > 0 && price <= stop) {
                    triggerType = "stop";
                    title = "🚨 " + name + " 손절 기준가 이탈!";
                    body = "현재가 " + pStr + " — 손실 방어를 위해 비중 점검이 필요합니다.";
                } else if (onMove && owned && Math.abs(ret1d) >= 5.0) {
                    triggerType = "move";
                    title = (ret1d > 0 ? "📈 " : "📉 ") + name + String.format(Locale.US, " %.1f%% 변동", ret1d);
                    body = "내 보유 종목 현재가 " + pStr + " — 앱에서 대응 가이드를 확인하세요.";
                } else if (onBuy && !owned && buyLow > 0 && buyHigh > 0 && price >= buyLow && price <= buyHigh) {
                    triggerType = "buy";
                    title = "🟢 " + name + " 분할매수 구간 진입!";
                    body = "현재가 " + pStr + " — 매수 구간에 들어왔습니다. 2~3회 나눠 접근해 보세요.";
                }

                if (triggerType != null) {
                    String dedupKey = KEY_SENT_PREFIX + todayKey + "_" + sym + "_" + triggerType;
                    if (!prefs.getBoolean(dedupKey, false)) {
                        int nid = Math.abs((sym + triggerType).hashCode()) % 100000 + 100;
                        postNotification(ctx, title, body, nid);
                        prefs.edit().putBoolean(dedupKey, true).apply();
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return Result.success();
    }
}
