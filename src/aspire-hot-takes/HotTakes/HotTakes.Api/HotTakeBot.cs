using System.Diagnostics;
using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using Npgsql;
using StackExchange.Redis;

/// <summary>Talks to the npu-agent container (an LLM running on the Intel NPU).</summary>
public class NpuAgentClient(HttpClient http, BotState state)
{
    public async Task<JsonObject> GetStatusAsync(CancellationToken ct)
    {
        if (!state.Enabled)
        {
            return new JsonObject { ["state"] = "disabled", ["detail"] = "AI agent disabled for this run" };
        }

        try
        {
            return await http.GetFromJsonAsync<JsonObject>("/status", ct) ?? [];
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or Polly.CircuitBreaker.BrokenCircuitException)
        {
            // The container is still being built or started on WSLc.
            return new JsonObject { ["state"] = "starting", ["detail"] = "starting the container on WSLc" };
        }
    }

    public async Task<NpuGeneration> GenerateAsync(IReadOnlyList<string> existing, CancellationToken ct)
    {
        using var response = await http.PostAsJsonAsync("/generate", new { existing }, ct);
        if (response.StatusCode == HttpStatusCode.ServiceUnavailable)
        {
            throw new NpuAgentNotReadyException();
        }
        response.EnsureSuccessStatusCode();
        return (await response.Content.ReadFromJsonAsync<NpuGeneration>(ct))!;
    }
}

public record NpuGeneration(string Text, string Topic, string Device, string DeviceName, string Model, int TtftMs, int TotalMs, int? Tokens);

public class NpuAgentNotReadyException() : Exception("The NPU agent is still warming up");

public class BotBusyException() : Exception("The NPU agent is already writing a take");

/// <summary>Shared state for the Hot Take Bot, read by the /bot endpoint and the UI.</summary>
public class BotState(IConfiguration config)
{
    public bool Enabled { get; } = config.GetValue("NpuAgent:Enabled", true);
    public int IntervalSeconds { get; } = config.GetValue("HotTakeBot:IntervalSeconds", 20);
    public int MaxPerRun { get; } = config.GetValue("HotTakeBot:MaxPerRun", 5);
    public SemaphoreSlim Busy { get; } = new(1, 1);

    public bool Autopilot { get; private set; }
    public int PostedThisRun { get; set; }
    public int PostedTotal { get; set; }
    public DateTimeOffset? NextRunAt { get; set; }
    public object? Last { get; set; }
    public string? LastError { get; set; }

    public void SetAutopilot(bool on)
    {
        Autopilot = on;
        PostedThisRun = 0;
        NextRunAt = on ? DateTimeOffset.UtcNow : null;
    }
}

/// <summary>
/// The agent loop: observe the leaderboard (Postgres) → think on the NPU (npu-agent) →
/// act by posting a new take (Postgres) and invalidating the leaderboard cache (Redis).
/// </summary>
public class HotTakeAgent(NpuAgentClient npu, NpgsqlDataSource db, IConnectionMultiplexer redis, BotState state, ILogger<HotTakeAgent> logger)
{
    public static readonly ActivitySource Source = new("HotTakes.Api");

    public async Task<object> RunCycleAsync(string trigger, CancellationToken ct)
    {
        if (!await state.Busy.WaitAsync(0, ct))
        {
            throw new BotBusyException();
        }

        using var activity = Source.StartActivity("HotTakeBot cycle");
        activity?.SetTag("hottakes.bot.trigger", trigger);
        try
        {
            // Observe: what's already on the board (most popular first)
            var existing = new List<string>();
            await using (var cmd = db.CreateCommand("SELECT text FROM takes ORDER BY votes DESC, id"))
            await using (var reader = await cmd.ExecuteReaderAsync(ct))
            {
                while (await reader.ReadAsync(ct))
                {
                    existing.Add(reader.GetString(0));
                }
            }

            for (var attempt = 1; ; attempt++)
            {
                // Think: ask the LLM on the NPU for something new
                var gen = await npu.GenerateAsync(existing, ct);
                activity?.SetTag("npu.device", gen.Device);
                activity?.SetTag("npu.device_name", gen.DeviceName);
                activity?.SetTag("npu.model", gen.Model);
                activity?.SetTag("npu.ttft_ms", gen.TtftMs);
                activity?.SetTag("npu.total_ms", gen.TotalMs);
                activity?.SetTag("npu.tokens", gen.Tokens);

                // Act: post it to the board and invalidate the cached leaderboard
                await using var insert = db.CreateCommand(
                    "INSERT INTO takes (text, votes, author, device, gen_ms) VALUES ($1, 0, 'npu-bot', $2, $3) ON CONFLICT (text) DO NOTHING RETURNING id");
                insert.Parameters.AddWithValue(gen.Text);
                insert.Parameters.AddWithValue(gen.Device);
                insert.Parameters.AddWithValue(gen.TotalMs);
                var id = await insert.ExecuteScalarAsync(ct);
                if (id is null || string.IsNullOrWhiteSpace(gen.Text))
                {
                    if (attempt < 2)
                    {
                        existing.Add(gen.Text);
                        continue;
                    }
                    throw new InvalidOperationException($"The model kept repeating an existing take: \"{gen.Text}\"");
                }

                await redis.GetDatabase().KeyDeleteAsync(Leaderboards.Key);

                logger.LogInformation("NPU bot posted \"{Take}\" in {TotalMs} ms on {Device} ({DeviceName}, {Model})",
                    gen.Text, gen.TotalMs, gen.Device, gen.DeviceName, gen.Model);

                var result = new
                {
                    id = (int)id, gen.Text, gen.Topic, gen.Device, gen.DeviceName, gen.Model,
                    gen.TtftMs, gen.TotalMs, gen.Tokens, at = DateTimeOffset.UtcNow,
                };
                state.Last = result;
                state.LastError = null;
                state.PostedTotal++;
                return result;
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            activity?.SetStatus(ActivityStatusCode.Error, ex.Message);
            state.LastError = ex.Message;
            throw;
        }
        finally
        {
            state.Busy.Release();
        }
    }
}

/// <summary>Autopilot: when switched on from the UI, posts a take every few seconds, then stops itself.</summary>
public class HotTakeBot(IServiceProvider services, BotState state, ILogger<HotTakeBot> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
        while (await timer.WaitForNextTickAsync(stoppingToken))
        {
            if (!state.Autopilot || state.NextRunAt > DateTimeOffset.UtcNow || state.Busy.CurrentCount == 0)
            {
                continue;
            }

            var delay = TimeSpan.FromSeconds(state.IntervalSeconds);
            try
            {
                await services.GetRequiredService<HotTakeAgent>().RunCycleAsync("autopilot", stoppingToken);
                state.PostedThisRun++;
                if (state.PostedThisRun >= state.MaxPerRun)
                {
                    logger.LogInformation("Autopilot posted {Count} takes, switching itself off", state.PostedThisRun);
                    state.SetAutopilot(false);
                    continue;
                }
            }
            catch (NpuAgentNotReadyException)
            {
                delay = TimeSpan.FromSeconds(3);
            }
            catch (BotBusyException)
            {
                delay = TimeSpan.FromSeconds(1);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                logger.LogWarning(ex, "Hot Take Bot cycle failed");
                delay = TimeSpan.FromSeconds(5);
            }

            if (state.Autopilot)
            {
                state.NextRunAt = DateTimeOffset.UtcNow + delay;
            }
        }
    }
}

static class Leaderboards
{
    public const string Key = "hottakes:leaderboard";
}
