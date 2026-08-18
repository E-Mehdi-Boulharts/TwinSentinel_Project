
#!/usr/bin/env python3
"""Red Agent - Offensive Attack Execution using LLM + MCP Tools"""
import asyncio
import json
import httpx

MCP_URL = "http://127.0.0.1:8000/mcp/"
OLLAMA_API = "http://localhost:11434/api/generate"
MODEL = "qwen:1.8b"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

class RedAgent:
    """Red Team - Offensive attack execution agent"""
    
    def __init__(self):
        self.name = "Red Agent"
        self.role = "Offensive Attack Execution"
        self.session_id = None
        self.request_id = 1
        self.available_attacks = [
            "traffic_light_tampering_attack",
            "universal_perturbation_attack",
            "targeted_adversarial_sensor_spoofing",
            "hopskipjump_attack",
            "poison_baseline_backdoor_attack",
            "poison_baseline_clean_label_attack",
            "sybil_attack",
            "fake_safety_message_attack",
            "fake_emergency_vehicle_broadcast",
            "knockoff_nets_extraction_attack",
            "attribute_inference_black_box_attack",
            "membership_inference_black_box_attack",
            "miface_model_inversion_attack",
            "database_reconstruction_attack"
        ]

    def _next_request_id(self) -> int:
        current = self.request_id
        self.request_id += 1
        return current

    def _session_headers(self) -> dict:
        headers = dict(MCP_HEADERS)
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        return headers

    def _parse_mcp_response(self, response: httpx.Response) -> dict:
        content_type = (response.headers.get("content-type") or "").lower()
        if "text/event-stream" in content_type:
            last_data = None
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    payload = line[len("data:"):].strip()
                    if payload:
                        last_data = payload
            if not last_data:
                return {"error": "Empty event-stream response", "raw": response.text}
            return json.loads(last_data)
        return response.json()

    async def _ensure_session(self) -> dict:
        """Initialize MCP streamable-http session if needed."""
        if self.session_id:
            return {"ok": True}

        init_payload = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "redagent", "version": "1.0.0"},
            },
        }

        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                init_response = await client.post(MCP_URL, json=init_payload, headers=MCP_HEADERS)
                if init_response.status_code != 200:
                    return {"ok": False, "error": f"HTTP {init_response.status_code}", "details": init_response.text}

                self.session_id = init_response.headers.get("mcp-session-id")
                if not self.session_id:
                    return {"ok": False, "error": "Missing mcp-session-id", "details": init_response.text}

                initialized_payload = {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
                await client.post(MCP_URL, json=initialized_payload, headers=self._session_headers())

            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    async def call_mcp_tool(self, tool_name: str, arguments: dict = None) -> dict:
        """Call a tool on the MCP server via JSON-RPC"""
        try:
            session = await self._ensure_session()
            if not session.get("ok"):
                return {"error": "MCP session init failed", "details": session}

            payload = {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments or {}
                }
            }
            
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.post(MCP_URL, json=payload, headers=self._session_headers())
                
                if response.status_code == 200:
                    result = self._parse_mcp_response(response)
                    # Extract the tool result
                    if "result" in result:
                        tool_result = result["result"]
                        content = tool_result.get("content") if isinstance(tool_result, dict) else None
                        if isinstance(content, list) and content:
                            text_payload = content[0].get("text")
                            if isinstance(text_payload, str):
                                try:
                                    return json.loads(text_payload)
                                except Exception:
                                    return {"text": text_payload}
                        return tool_result
                    return result
                else:
                    return {"error": f"HTTP {response.status_code}", "details": response.text}
        except Exception as e:
            return {"error": str(e), "tool": tool_name}
        
    async def get_ai_decision(self, prompt: str) -> str:
        """Get decision from LLM via Ollama"""
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                response = await client.post(
                    OLLAMA_API,
                    json={"model": MODEL, "prompt": prompt, "stream": False},
                    timeout=60
                )
                if response.status_code == 200:
                    result = response.json().get("response", "").strip()
                    return result if result else "Planning..."
        except Exception as e:
            return f"[LLM error: {type(e).__name__}]"
        return "No response"
    
    async def test_server_connection(self) -> bool:
        """Test MCP server connection"""
        try:
            session = await self._ensure_session()
            if not session.get("ok"):
                return False

            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                response = await client.post(
                    MCP_URL,
                    json={"jsonrpc": "2.0", "id": self._next_request_id(), "method": "tools/list"},
                    headers=self._session_headers(),
                )
                return response.status_code < 500
        except:
            return False
    
    async def run(self):
        """Run interactive red agent"""
        print("=" * 70)
        print(f"🔴 {self.name} - {self.role}")
        print("=" * 70)
        print()
        
        # Check server connection
        if not await self.test_server_connection():
            print(f"❌ Cannot connect to MCP server at {MCP_URL}")
            print(f"   Start the server with: python3 MCP_server.py")
            print()
            return
        
        print(f"✅ Connected to MCP server at {MCP_URL}")
        print()
        print("🎯 Available Attack Tools:")
        for i, attack in enumerate(self.available_attacks, 1):
            print(f"   {i}. {attack}")
        print()
        print("Commands:")
        print("  help       - Show available attacks")
        print("  light      - Traffic light tampering attack")
        print("  universal  - Universal perturbation attack")
        print("  adversarial- Targeted adversarial sensor spoofing attack")
        print("  hopskipjump- HopSkipJump decision-based black-box attack")
        print("  backdoor   - Backdoor Attack (poisons a saved baseline)")
        print("  cleanlabel - Clean Label Feature Collision Attack (poisons a saved baseline)")
        print("  sybil      - Sybil attack (fake vehicles)")
        print("  safety     - Fake safety message attack")
        print("  emergency  - Fake emergency vehicle broadcast")
        print("  extract    - KnockoffNets model extraction attack (no simulation required)")
        print("  infer      - Attribute Inference Black-Box attack (infers a vehicle's sensitive attribute, no simulation required)")
        print("  member     - Membership Inference Black-Box attack (detects if a record was in the training set, no simulation required)")
        print("  invert     - MIFace model inversion attack (reconstructs a representative safe/unsafe vehicle state, no simulation required)")
        print("  reconstruct- Database Reconstruction attack (recovers a specific withheld training row, no simulation required)")
        print("  exit/quit  - Exit agent")
        print("  note       - Attacks persist only while the simulation is running")
        print()
        print("=" * 70)
        print()
        
        # Interactive loop
        while True:
            try:
                cmd = input("🔴 > ").strip().lower()
                if not cmd:
                    continue
                    
                if cmd in ["exit", "quit"]:
                    print("👋 Goodbye!")
                    break
                
                if cmd == "help":
                    print("\n🎯 Attacks:")
                    print("   light       -> traffic_light_tampering_attack")
                    print("   universal   -> universal_perturbation_attack")
                    print("   adversarial -> targeted_adversarial_sensor_spoofing")
                    print("   hopskipjump -> hopskipjump_attack")
                    print("   backdoor    -> poison_baseline_backdoor_attack")
                    print("   cleanlabel  -> poison_baseline_clean_label_attack")
                    print("   sybil       -> sybil_attack")
                    print("   safety      -> fake_safety_message_attack")
                    print("   emergency   -> fake_emergency_vehicle_broadcast")
                    print("   extract     -> knockoff_nets_extraction_attack")
                    print("   infer       -> attribute_inference_black_box_attack")
                    print("   member      -> membership_inference_black_box_attack")
                    print("   invert      -> miface_model_inversion_attack")
                    print("   reconstruct -> database_reconstruction_attack")
                    print()
                    continue
                
                # Attack execution
                result = None
                
                if cmd == "light":
                    print("⏳ Executing traffic light tampering attack...")
                    result = await self.call_mcp_tool("traffic_light_tampering_attack", {"params": {"duration": 30}})

                elif cmd == "universal":
                    print("⏳ Executing universal perturbation attack...")
                    result = await self.call_mcp_tool(
                        "universal_perturbation_attack",
                        {"params": {"duration": 30, "epsilon": 0.3, "scale_position": 0.5, "scale_velocity": 0.3}},
                    )
                
                elif cmd == "adversarial":
                    print("🔴 Executing adversarial sensor spoofing attack (REALISTIC)...")
                    result = await self.call_mcp_tool(
                        "targeted_adversarial_sensor_spoofing",
                        {"params": {"duration": 30, "num_obstacles": 2}},
                    )
                    if "obstacle_ids" in result:
                        print(f"   ✓ Obstacles created: {result['obstacle_count']}")
                        print(f"   ✓ Vehicles will detect via sensors and brake naturally")
                        print(f"   ✓ {result['target_count']} vehicles targeted")
                        print(f"   ✓ Attack duration: {result['duration']}s")

                elif cmd == "hopskipjump":
                    print("⏳ Executing HopSkipJump decision-based black-box attack...")
                    result = await self.call_mcp_tool(
                        "hopskipjump_attack",
                        {"params": {"duration": 30, "num_targets": 3, "max_iterations": 15}},
                    )

                elif cmd == "backdoor":
                    print("⏳ Executing Backdoor Attack (poisons a saved baseline copy)...")
                    result = await self.call_mcp_tool(
                        "poison_baseline_backdoor_attack",
                        {"params": {"fraction_poisoned": 0.15}},
                    )

                elif cmd == "cleanlabel":
                    print("⏳ Executing Clean Label Feature Collision Attack (poisons a saved baseline copy)...")
                    result = await self.call_mcp_tool(
                        "poison_baseline_clean_label_attack",
                        {"params": {"epsilon": 0.15}},
                    )

                elif cmd == "extract":
                    print("🔓 Executing KnockoffNets model extraction attack (no simulation required)...")
                    result = await self.call_mcp_tool(
                        "knockoff_nets_extraction_attack",
                        {"params": {"target_model": "safety", "query_budget": 500, "strategy": "adaptive"}},
                    )

                elif cmd == "infer":
                    print("🕵️ Executing Attribute Inference Black-Box attack (no simulation required)...")
                    result = await self.call_mcp_tool(
                        "attribute_inference_black_box_attack",
                        {"params": {"target_attribute": "road_type", "aux_size": 3000, "eval_size": 1000}},
                    )

                elif cmd == "member":
                    print("🕵️ Executing Membership Inference Black-Box attack (no simulation required)...")
                    result = await self.call_mcp_tool(
                        "membership_inference_black_box_attack",
                        {"params": {"target_train_size": 60, "target_holdout_size": 300, "num_shadow_models": 12}},
                    )

                elif cmd == "invert":
                    print("Executing MIFace model inversion attack (no simulation required)...")
                    result = await self.call_mcp_tool(
                        "miface_model_inversion_attack",
                        {"params": {"target_classes": [0, 1], "max_iterations": 300}},
                    )

                elif cmd == "reconstruct":
                    print("Executing Database Reconstruction attack (no simulation required)...")
                    result = await self.call_mcp_tool(
                        "database_reconstruction_attack",
                        {"params": {"known_size": 39, "max_iterations": 80}},
                    )

                elif cmd == "sybil":
                    print("⏳ Executing Sybil attack...")
                    result = await self.call_mcp_tool("sybil_attack", {"params": {"count": 5, "duration": 30}})
                
                elif cmd == "safety":
                    print("⏳ Executing fake safety message attack...")
                    result = await self.call_mcp_tool("fake_safety_message_attack", {"params": {"duration": 30}})
                
                elif cmd == "emergency":
                    print("⏳ Executing fake emergency vehicle broadcast...")
                    result = await self.call_mcp_tool("fake_emergency_vehicle_broadcast", {"params": {"duration": 30}})

                else:
                    print(f"❓ Unknown command: {cmd}")
                    print("   Type 'help' for available attacks")
                    continue
                
                # Display result
                if result:
                    print("\n✅ Attack Result:")
                    if isinstance(result, dict):
                        for key, value in result.items():
                            print(f"   {key}: {value}")
                    else:
                        print(f"   {result}")
                print()
                
            except KeyboardInterrupt:
                print("\n👋 Interrupted")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                print()


async def main():
    agent = RedAgent()
    await agent.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ Shutdown")
