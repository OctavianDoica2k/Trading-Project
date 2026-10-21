//+------------------------------------------------------------------+
//|                                               Nexus_TrendMTF.mq5 |
//|  Indicatorul „Trend multi-timeframe” al aplicației Nexus, MT5.   |
//+------------------------------------------------------------------+
// =====================================================================================
//  CE FACE ACEST FIȘIER (pe scurt)
// -------------------------------------------------------------------------------------
//  - Același calcul ca timeframe_trends_indicator(df) din analysis.py, pe lumânările D1
//    ÎNCHISE ale anului curent (prețul live nu intră niciodată în calcul):
//      1Y = (close-ul ultimei zile − open-ul primei zile a anului) ÷ (maximul − minimul anului)
//      1M = la fel, de la prima zi a lunii ultimei zile
//      1W = de la începutul săptămânii (ultima pauză > 1 zi sau ziua 1 a lunii, cel mult 5
//           zile în urmă), împărțit — ca în analysis.py — la maximul − minimul LUNII
//           (InpWeekMonthRange = false: la maximul − minimul săptămânii)
//      1D = (close − open) ÷ (high − low) al ultimei zile închise
//    totul × 100, rotunjit la 2 zecimale. Un maxim egal cu minimul dă „—” (EMPTY_VALUE).
//  - Merge pe orice timeframe al chartului. Pentru fiecare lumânare, „ultima zi închisă” e
//    ultima lumânare D1 care începe înaintea zilei lumânării, deci istoricul din buffere e
//    exact ce s-ar fi văzut atunci (fără repictare) — EA-ul îl folosește prin iCustom.
//  - Panoul: cele patru casete (verde = creștere, roșu = scădere, intensitatea după procent),
//    marcajul de aliniere („▲ 4/4 aliniate” / „Mixt ▲2 ▼2”, cu pragul InpPrag) și ziua până la
//    care sunt datele. Cu mouse-ul peste o casetă apar detaliile calculului.
// =====================================================================================
#property copyright   "Nexus"
#property version     "1.00"
#property description "Trend multi-timeframe (timeframe_trends_indicator din analysis.py): 1D · 1W · 1M · 1Y din lumânările D1 închise."
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   4
#property indicator_label1  "MTF 1D"
#property indicator_type1   DRAW_NONE
#property indicator_label2  "MTF 1W"
#property indicator_type2   DRAW_NONE
#property indicator_label3  "MTF 1M"
#property indicator_type3   DRAW_NONE
#property indicator_label4  "MTF 1Y"
#property indicator_type4   DRAW_NONE

input double InpPrag           = 0;     // Pragul alinierii, % (0 = fără prag)
input bool   InpWeekMonthRange = true;  // 1W împărțit la maximul − minimul lunii (ca analysis.py)
input bool   InpShowPanel      = true;  // Arată panoul cu cele patru casete
input int    InpX              = 10;    // Panoul: distanța față de marginea stângă (px)
input int    InpY              = 25;    // Panoul: distanța față de marginea de sus (px)
input int    InpHistoryBars    = 0;     // Lumânări de istoric calculate în buffere (0 = tot istoricul)

double DayB[], WeekB[], MonthB[], YearB[];

MqlRates gD1[];
int      gD1Count = 0;       // câte lumânări D1 avea terminalul la ultima copiere
datetime gD1Last  = 0;       // ora ultimei lumânări D1 la ultima copiere
int      gCacheK  = -2;      // ultima zi calculată (index în gD1) și valorile ei
double   gCache[4];
int      gPanelK  = -2;      // ziua afișată acum în panou
const string PREFIX = "NexusMTF_";
const string KEYS[4] = {"1D", "1W", "1M", "1Y"};

//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0, DayB, INDICATOR_DATA);
   SetIndexBuffer(1, WeekB, INDICATOR_DATA);
   SetIndexBuffer(2, MonthB, INDICATOR_DATA);
   SetIndexBuffer(3, YearB, INDICATOR_DATA);
   for(int p = 0; p < 4; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   IndicatorSetString(INDICATOR_SHORTNAME, "Trend MTF");
   IndicatorSetInteger(INDICATOR_DIGITS, 2);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   ObjectsDeleteAll(0, PREFIX);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
//| Lumânările D1                                                    |
//+------------------------------------------------------------------+
int YearOf(const datetime t)  { MqlDateTime d; TimeToStruct(t, d); return d.year; }
int MonthOf(const datetime t) { MqlDateTime d; TimeToStruct(t, d); return d.mon; }
int DayOf(const datetime t)   { MqlDateTime d; TimeToStruct(t, d); return d.day; }
string Day(const datetime t)  { return TimeToString(t, TIME_DATE); }

// Copiază lumânările D1 (cea mai veche prima) doar când s-au schimbat.
bool LoadD1()
  {
   int n = Bars(_Symbol, PERIOD_D1);
   if(n <= 0)
      return false;
   datetime last = (datetime)SeriesInfoInteger(_Symbol, PERIOD_D1, SERIES_LASTBAR_DATE);
   if(n == gD1Count && last == gD1Last && ArraySize(gD1) > 0)
      return true;
   MqlRates tmp[];
   int got = CopyRates(_Symbol, PERIOD_D1, 0, n, tmp);
   if(got <= 0)
      return false;
   ArraySwap(gD1, tmp);
   gD1Count = n;
   gD1Last = last;
   gCacheK = -2;
   gPanelK = -2;
   return true;
  }

// Ultima zi închisă înaintea zilei care începe la dayStart (index în gD1; -1 = niciuna).
int LastClosedDay(const datetime dayStart)
  {
   int lo = 0, hi = ArraySize(gD1) - 1, ans = -1;
   while(lo <= hi)
     {
      int mid = (lo + hi) >> 1;
      if(gD1[mid].time < dayStart) { ans = mid; lo = mid + 1; }
      else                         hi = mid - 1;
     }
   return ans;
  }

// round((a / b) * 100, 2) din analysis.py; b = 0 (maxim = minim) dă EMPTY_VALUE („—”).
double Pct(const double a, const double b)
  {
   if(b == 0.0)
      return EMPTY_VALUE;
   return MathRound(a / b * 100.0 * 100.0) / 100.0;
  }

// Indexul iloc[j] al unui tabel de len rânduri, cu indicii negativi ca în pandas; -1 = IndexError.
int Wrap(const int j, const int len)
  {
   if(j >= 0 && j < len) return j;
   if(j < 0 && j >= -len) return len + j;
   return -1;
  }

string Fx(const double v) { return DoubleToString(v, _Digits); }

string PctText(const double v)
  {
   if(v == EMPTY_VALUE) return "—";
   return (v > 0 ? "+" : v < 0 ? "−" : "") + DoubleToString(MathAbs(v), 2) + "%";
  }

string Detail(const string key, const int a, const int b, const double hi, const double lo,
              const double v, const string note)
  {
   string s = key + ": " + (a == b ? Day(gD1[a].time) : Day(gD1[a].time) + " → " + Day(gD1[b].time) +
              " · " + IntegerToString(b - a + 1) + " zile") +
              "\nopen " + Fx(gD1[a].open) + " · close " + Fx(gD1[b].close) +
              "\nhigh " + Fx(hi) + " · low " + Fx(lo) + "\n";
   if(v == EMPTY_VALUE)
      s += "— maximul este egal cu minimul (împărțire la 0)";
   else
      s += "(" + Fx(gD1[b].close) + " − " + Fx(gD1[a].open) + ") ÷ (" + Fx(hi) + " − " + Fx(lo) + ") × 100 = " + PctText(v);
   if(note != "")
      s += "\n" + note;
   return s;
  }

// Cele patru valori (1D, 1W, 1M, 1Y) cu ultima zi închisă k; detaliile doar pentru panou.
void Compute(const int k, double &vals[], string &det[], const bool withDetails)
  {
   for(int i = 0; i < 4; i++) { vals[i] = EMPTY_VALUE; if(withDetails) det[i] = "—"; }
   if(k < 0)
      return;
   int yr = YearOf(gD1[k].time), mo = MonthOf(gD1[k].time);
   // Anul: zilele închise din anul ultimei zile (df din analysis.py).
   int y0 = k;
   while(y0 > 0 && YearOf(gD1[y0 - 1].time) == yr) y0--;
   double yhi = gD1[y0].high, ylo = gD1[y0].low;
   for(int i = y0 + 1; i <= k; i++) { yhi = MathMax(yhi, gD1[i].high); ylo = MathMin(ylo, gD1[i].low); }
   // Luna: de la prima zi a lunii (lastmonth).
   int m0 = k;
   while(m0 > y0 && MonthOf(gD1[m0 - 1].time) == mo) m0--;
   double mhi = gD1[m0].high, mlo = gD1[m0].low;
   for(int i = m0 + 1; i <= k; i++) { mhi = MathMax(mhi, gD1[i].high); mlo = MathMin(mlo, gD1[i].low); }

   vals[3] = Pct(gD1[k].close - gD1[y0].open, yhi - ylo);
   vals[2] = Pct(gD1[k].close - gD1[m0].open, mhi - mlo);
   vals[0] = Pct(gD1[k].close - gD1[k].open, gD1[k].high - gD1[k].low);

   // Săptămâna: bucla din analysis.py, cu indicii lui pandas (iloc[-1] = ultimul rând al lunii).
   int len = k - m0 + 1, i = len - 1;
   bool brk = false, err = false;
   for(i = len - 1; i > len - 6; i--)
     {
      int a = Wrap(i, len), b = Wrap(i - 1, len);
      if(a < 0 || b < 0) { err = true; break; }
      datetime ta = gD1[m0 + a].time, tb = gD1[m0 + b].time;
      if((long)ta - (long)tb > 86400 || DayOf(ta) == 1) { brk = true; break; }
     }
   if(!brk && !err)
      i = len - 5;                     // în Python, variabila buclei rămâne la ultima valoare
   int w0 = -1;
   double whi = mhi, wlo = mlo;
   if(!err)
     {
      w0 = m0 + MathMax(0, i);         // lastmonth[index >= i]
      if(!InpWeekMonthRange)
        {
         whi = gD1[w0].high; wlo = gD1[w0].low;
         for(int j = w0 + 1; j <= k; j++) { whi = MathMax(whi, gD1[j].high); wlo = MathMin(wlo, gD1[j].low); }
        }
      vals[1] = Pct(gD1[k].close - gD1[w0].open, whi - wlo);
     }

   if(!withDetails)
      return;
   det[0] = Detail("1D", k, k, gD1[k].high, gD1[k].low, vals[0], "1D = ultima zi închisă (ziua în curs nu intră în calcul).");
   if(err)
      det[1] = "1W: — IndexError (prea puține zile în lună pentru bucla din analysis.py)";
   else
     {
      string note = "";
      if(InpWeekMonthRange)
        {
         double ohi = gD1[w0].high, olo = gD1[w0].low;
         for(int j = w0 + 1; j <= k; j++) { ohi = MathMax(ohi, gD1[j].high); olo = MathMin(olo, gD1[j].low); }
         if(ohi != mhi || olo != mlo)
            note = "Maximul și minimul sunt ale lunii (analysis.py folosește lastmonth). Ale săptămânii: " +
                   Fx(ohi) + " / " + Fx(olo) + ".";
        }
      det[1] = Detail("1W", w0, k, whi, wlo, vals[1], note);
     }
   det[2] = Detail("1M", m0, k, mhi, mlo, vals[2], "");
   det[3] = Detail("1Y", y0, k, yhi, ylo, vals[3], "1Y = de la 1 ianuarie (anul în curs), nu ultimele 12 luni.");
  }

//+------------------------------------------------------------------+
//| Panoul                                                           |
//+------------------------------------------------------------------+
color Blend(const color a, const color b, const double t)
  {
   int ar = (int)(a & 0xFF), ag = (int)((a >> 8) & 0xFF), ab = (int)((a >> 16) & 0xFF);
   int br = (int)(b & 0xFF), bg = (int)((b >> 8) & 0xFF), bb = (int)((b >> 16) & 0xFF);
   int r = (int)MathRound(ar + (br - ar) * t), g = (int)MathRound(ag + (bg - ag) * t), bl = (int)MathRound(ab + (bb - ab) * t);
   return (color)(r | (g << 8) | (bl << 16));
  }

void Rect(const string name, const int x, const int y, const int w, const int h, const color bg, const color border)
  {
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, border);
  }

void Text(const string name, const int x, const int y, const string txt, const color clr, const int size,
          const string font = "Segoe UI", const ENUM_ANCHOR_POINT anchor = ANCHOR_LEFT_UPPER, const string tip = "")
  {
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tip == "" ? "\n" : tip);
  }

void DrawPanel(const int k)
  {
   if(!InpShowPanel || k == gPanelK)
      return;
   gPanelK = k;
   double v[4];
   string det[4];
   Compute(k, v, det, true);
   const color BG = C'24,26,32', BOX = C'38,41,50', LINE = C'60,64,76';
   const color UP = C'38,166,91', DN = C'230,72,72', TXT = C'220,222,228', MUTED = C'130,134,146';
   int bw = 74, bh = 46, gap = 6, pad = 8;
   int W = pad * 2 + bw * 4 + gap * 3, H = 26 + bh + 24;
   Rect(PREFIX + "bg", InpX, InpY, W, H, BG, LINE);
   Text(PREFIX + "title", InpX + pad, InpY + 6, "Trend MTF", TXT, 9, "Segoe UI Semibold", ANCHOR_LEFT_UPPER,
        "Trend multi-timeframe: timeframe_trends_indicator din analysis.py, pe lumânările D1 închise");

   // Marcajul de aliniere (aceeași regulă ca în aplicație).
   int pos = 0, neg = 0;
   for(int i = 0; i < 4; i++)
     {
      if(v[i] == EMPTY_VALUE) continue;
      if(v[i] > 0 && v[i] >= InpPrag) pos++;
      if(v[i] < 0 && -v[i] >= InpPrag) neg++;
     }
   string pill;
   color pc;
   if(pos == 4)      { pill = "▲ 4/4 aliniate"; pc = UP; }
   else if(neg == 4) { pill = "▼ 4/4 aliniate"; pc = DN; }
   else              { pill = "Mixt ▲" + IntegerToString(pos) + " ▼" + IntegerToString(neg); pc = MUTED; }
   if(InpPrag > 0)
      pill += " · prag ≥ " + DoubleToString(InpPrag, 0) + "%";
   Text(PREFIX + "pill", InpX + W - pad, InpY + 6, pill, pc, 9, "Segoe UI Semibold", ANCHOR_RIGHT_UPPER,
        "Este o informație, nu o recomandare de tranzacționare.");

   for(int i = 0; i < 4; i++)
     {
      int x = InpX + pad + i * (bw + gap), y = InpY + 26;
      bool ok = v[i] != EMPTY_VALUE;
      double t = ok ? MathMin(1.0, MathAbs(v[i]) / 100.0) : 0;
      bool weak = ok && InpPrag > 0 && MathAbs(v[i]) < InpPrag;
      color bg = !ok || v[i] == 0 ? BOX : Blend(BOX, v[i] > 0 ? UP : DN, (weak ? 0.08 : 0.15) + (weak ? 0.2 : 0.6) * t);
      Rect(PREFIX + "box" + IntegerToString(i), x, y, bw, bh, bg, LINE);
      ObjectSetString(0, PREFIX + "box" + IntegerToString(i), OBJPROP_TOOLTIP, det[i]);
      Text(PREFIX + "k" + IntegerToString(i), x + 6, y + 4, KEYS[i], weak ? MUTED : TXT, 8, "Segoe UI", ANCHOR_LEFT_UPPER, det[i]);
      Text(PREFIX + "v" + IntegerToString(i), x + bw / 2, y + 20, PctText(v[i]), weak ? MUTED : TXT, 10,
           "Segoe UI Semibold", ANCHOR_UPPER, det[i]);
     }
   string foot = k >= 0 ? "D1 închis până la " + Day(gD1[k].time) : "Nicio zi D1 închisă";
   Text(PREFIX + "foot", InpX + pad, InpY + 26 + bh + 5, foot, MUTED, 8, "Segoe UI", ANCHOR_LEFT_UPPER,
        "Doar zile închise, nu prețul live. " + IntegerToString(ArraySize(gD1)) + " lumânări D1 în terminal.");
   ChartRedraw();
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[], const double &high[],
                const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   if(!LoadD1())
      return 0;                       // istoricul D1 nu e încă disponibil: încercăm la următorul tick
   int start;
   if(prev_calculated > 0)
      start = prev_calculated - 1;
   else
     {
      ArrayInitialize(DayB, EMPTY_VALUE);
      ArrayInitialize(WeekB, EMPTY_VALUE);
      ArrayInitialize(MonthB, EMPTY_VALUE);
      ArrayInitialize(YearB, EMPTY_VALUE);
      start = InpHistoryBars > 0 ? MathMax(0, rates_total - InpHistoryBars) : 0;
     }
   string none[];
   int k = -1;
   for(int b = start; b < rates_total; b++)
     {
      datetime dayStart = time[b] - (time[b] % 86400);
      k = LastClosedDay(dayStart);
      if(k != gCacheK)
        {
         Compute(k, gCache, none, false);
         gCacheK = k;
        }
      DayB[b] = gCache[0];
      WeekB[b] = gCache[1];
      MonthB[b] = gCache[2];
      YearB[b] = gCache[3];
     }
   DrawPanel(k);
   return rates_total;
  }
//+------------------------------------------------------------------+
